"""Publish verified Windows installers through GitHub without exposing credentials.

Run ``check`` before creating a draft, ``prepare`` to upload its artifacts, then
``publish --release-id ID`` to publish that exact draft. All phases require the
repository, tag, pushed commit, installer, and release-note file explicitly.
Authentication comes from Git Credential Manager; no token is written to disk.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from typing import Any
import urllib.error
import urllib.parse
import urllib.request


class ReleaseError(Exception):
    """A validation failure safe to display without credential details."""


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ReleaseError("GitHub unexpectedly redirected an authenticated request")


class GitHub:
    def __init__(self, repo: str):
        environment = dict(os.environ, GIT_TERMINAL_PROMPT="0", GCM_INTERACTIVE="Never")
        try:
            result = subprocess.run(
                ["git", "credential", "fill"],
                input="protocol=https\nhost=github.com\n\n",
                text=True,
                capture_output=True,
                env=environment,
                timeout=30,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            raise ReleaseError("Cannot read the GitHub credential from Git") from error
        credential = dict(line.split("=", 1) for line in result.stdout.splitlines() if "=" in line)
        self._token = credential.get("password")
        if result.returncode or not self._token:
            raise ReleaseError("GitHub authentication is unavailable in Git Credential Manager")
        self.base = f"https://api.github.com/repos/{repo}"
        self.opener = urllib.request.build_opener(NoRedirect)

    def request(
        self,
        path: str,
        *,
        method: str = "GET",
        payload: dict[str, Any] | None = None,
        artifact: Path | None = None,
        allow_missing: bool = False,
    ) -> Any:
        url = path if path.startswith("https://") else self.base + path
        parsed = urllib.parse.urlparse(url)
        if parsed.scheme != "https" or parsed.hostname not in {"api.github.com", "uploads.github.com"}:
            raise ReleaseError("Refusing to send GitHub authentication to another host")
        headers = {
            "Authorization": f"Bearer {self._token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "DistributedExec-release-publisher",
        }
        if artifact is not None:
            data = artifact.read_bytes()
            headers["Content-Type"] = "application/octet-stream"
        elif payload is not None:
            data = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"
        else:
            data = None
        request = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with self.opener.open(request, timeout=180 if artifact else 30) as response:
                return json.load(response)
        except urllib.error.HTTPError as error:
            if allow_missing and error.code == 404:
                return None
            raise ReleaseError(f"GitHub rejected {method} {parsed.path} (HTTP {error.code})") from None
        except (urllib.error.URLError, TimeoutError) as error:
            raise ReleaseError("GitHub request failed; rerun check before retrying") from error


def git(*arguments: str) -> str:
    result = subprocess.run(["git", *arguments], capture_output=True, text=True, check=False)
    if result.returncode:
        raise ReleaseError(f"Git validation failed: {arguments[0]}")
    return result.stdout.strip()


def sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def validate_local(args: argparse.Namespace) -> tuple[str, list[Path], str]:
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", args.repo):
        raise ReleaseError("Repository must use the owner/name format")
    if not re.fullmatch(r"v\d+\.\d+\.\d+", args.tag):
        raise ReleaseError("Tag must use the vMAJOR.MINOR.PATCH format")
    remote = git("remote", "get-url", "origin").removesuffix(".git")
    if remote not in {f"https://github.com/{args.repo}", f"git@github.com:{args.repo}"}:
        raise ReleaseError("The repository argument does not match Git origin")
    commit = git("rev-parse", "--verify", args.commit + "^{commit}")
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise ReleaseError("Cannot resolve the intended release commit")
    if git("rev-parse", "HEAD") != commit:
        raise ReleaseError("Release commit must be the current checkout")
    if git("status", "--porcelain"):
        raise ReleaseError("Commit all source changes before preparing the release")
    installer = args.artifact.resolve(strict=True)
    expected_name = f"DistributedExec-{args.tag[1:]}-windows-x64-setup.exe"
    if not installer.is_file() or installer.name != expected_name or installer.stat().st_size == 0:
        raise ReleaseError(f"Installer must be a nonempty file named {expected_name}")
    checksum = Path(str(installer) + ".sha256")
    if not checksum.is_file():
        raise ReleaseError("Installer checksum file is missing")
    expected_checksum = f"{sha256(installer)}  {installer.name}"
    if checksum.read_text(encoding="ascii").strip() != expected_checksum:
        raise ReleaseError("Installer checksum does not match its .sha256 file")
    body = args.body_file.read_text(encoding="utf-8-sig").strip()
    if not body:
        raise ReleaseError("Release notes must not be empty")
    return commit, [installer, checksum], body


def validate_remote(api: GitHub, args: argparse.Namespace, commit: str) -> dict[str, Any] | None:
    repository = api.request("")
    if not repository.get("permissions", {}).get("push"):
        raise ReleaseError("GitHub authentication does not have push access")
    branch = urllib.parse.quote(repository["default_branch"], safe="")
    if api.request(f"/commits/{branch}")["sha"] != commit:
        raise ReleaseError("Push the intended release commit to the default branch first")
    tag_path = urllib.parse.quote(args.tag, safe="")
    reference = api.request(f"/git/ref/tags/{tag_path}", allow_missing=True)
    if reference is not None:
        target = reference["object"]
        for _ in range(5):
            if target["type"] != "tag":
                break
            target = api.request(f"/git/tags/{target['sha']}")["object"]
        if target["type"] != "commit" or target["sha"] != commit:
            raise ReleaseError("Existing tag points to a different commit; nothing was changed")
    release = api.request(f"/releases/tags/{tag_path}", allow_missing=True)
    if release is None:
        # GitHub's by-tag endpoint omits drafts, even for their authenticated owner.
        matches = []
        page = 1
        while True:
            releases = api.request(f"/releases?per_page=100&page={page}")
            matches.extend(candidate for candidate in releases if candidate["tag_name"] == args.tag)
            if len(releases) < 100:
                break
            page += 1
        if len(matches) > 1:
            raise ReleaseError("Multiple releases use this tag; nothing was changed")
        release = matches[0] if matches else None
    if release is not None and release["target_commitish"] != commit:
        raise ReleaseError("Existing release has a different target; nothing was changed")
    return release


def validate_assets(release: dict[str, Any], artifacts: list[Path], *, require_all: bool) -> None:
    assets = {asset["name"]: asset for asset in release["assets"]}
    expected_names = {artifact.name for artifact in artifacts}
    if set(assets) - expected_names:
        raise ReleaseError("Existing release contains unrelated artifacts; nothing was changed")
    for artifact in artifacts:
        asset = assets.get(artifact.name)
        if asset is None:
            if require_all:
                raise ReleaseError(f"Release is missing {artifact.name}")
            continue
        if (
            asset["state"] != "uploaded"
            or asset["size"] != artifact.stat().st_size
            or asset.get("digest") != "sha256:" + sha256(artifact)
        ):
            raise ReleaseError(f"Uploaded {artifact.name} differs from the local verified file")


def summary(release: dict[str, Any] | None, commit: str) -> dict[str, Any]:
    if release is None:
        return {"commit": commit, "release": None, "ready_for_draft": True}
    return {
        "commit": commit,
        "release_id": release["id"],
        "tag": release["tag_name"],
        "draft": release["draft"],
        "url": release["html_url"],
        "assets": [{"name": asset["name"], "size": asset["size"], "digest": asset.get("digest")} for asset in release["assets"]],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["check", "prepare", "publish"])
    parser.add_argument("--repo", required=True)
    parser.add_argument("--tag", required=True)
    parser.add_argument("--commit", required=True)
    parser.add_argument("--artifact", type=Path, required=True, help="Installer; its .sha256 file is included automatically")
    parser.add_argument("--body-file", type=Path, required=True)
    parser.add_argument("--release-id", type=int, help="Exact draft ID required when publishing")
    args = parser.parse_args()
    if args.command == "publish" and args.release_id is None:
        parser.error("publish requires --release-id")
    try:
        commit, artifacts, body = validate_local(args)
        api = GitHub(args.repo)
        release = validate_remote(api, args, commit)
        if release is not None:
            if (release.get("body") or "").strip() != body:
                raise ReleaseError("Existing release notes differ from the supplied file; nothing was changed")
            validate_assets(release, artifacts, require_all=args.command == "publish")
        if args.command == "check":
            print(json.dumps(summary(release, commit), indent=2))
            return 0
        if args.command == "prepare":
            if release is not None and not release["draft"]:
                raise ReleaseError("Release is already published; existing assets will not be replaced")
            if release is None:
                release = api.request("/releases", method="POST", payload={
                    "tag_name": args.tag,
                    "target_commitish": commit,
                    "name": f"DistributedExec {args.tag[1:]}",
                    "body": body,
                    "draft": True,
                    "prerelease": False,
                })
            existing = {asset["name"] for asset in release["assets"]}
            upload_url = release["upload_url"].split("{", 1)[0]
            for artifact in artifacts:
                if artifact.name not in existing:
                    url = upload_url + "?" + urllib.parse.urlencode({"name": artifact.name})
                    api.request(url, method="POST", artifact=artifact)
            release = api.request(f"/releases/{release['id']}")
            validate_assets(release, artifacts, require_all=True)
        else:
            if release is None or release["id"] != args.release_id:
                raise ReleaseError("The expected release ID does not match the tag")
            if release["draft"]:
                release = api.request(f"/releases/{release['id']}", method="PATCH", payload={"draft": False, "make_latest": "true"})
            validate_assets(release, artifacts, require_all=True)
        print(json.dumps(summary(release, commit), indent=2))
        return 0
    except (ReleaseError, OSError, UnicodeError) as error:
        print(f"Release stopped: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
