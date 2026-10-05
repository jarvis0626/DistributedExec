import os
import socket
import subprocess
import sys
import time
from pathlib import Path
import pytest
import requests
from distributedexec.credentials import admin_secret

pytestmark = pytest.mark.browser


@pytest.fixture
def browser_host(tmp_path):
    playwright = pytest.importorskip('playwright.sync_api')
    edge = Path(os.environ.get('PROGRAMFILES(X86)', 'C:/Program Files (x86)')) / 'Microsoft/Edge/Application/msedge.exe'
    browser_path = os.environ.get('DISTRIBUTEDEXEC_BROWSER') or (str(edge) if edge.exists() else None)
    if not browser_path:
        pytest.skip('Set DISTRIBUTEDEXEC_BROWSER to an installed Chromium browser executable')
    with socket.socket() as probe:
        probe.bind(('127.0.0.1', 0))
        port = probe.getsockname()[1]
    url = f'http://127.0.0.1:{port}'
    workspace = tmp_path / 'workspace'
    token = admin_secret(workspace)
    process = subprocess.Popen([sys.executable, 'host.py', '--workspace', str(workspace), '--bind', '127.0.0.1', '--port', str(port)],
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
    try:
        for _ in range(100):
            try:
                requests.get(url + '/health', timeout=.5).raise_for_status()
                break
            except requests.RequestException:
                time.sleep(.1)
        with playwright.sync_playwright() as p:
            browser = p.chromium.launch(executable_path=browser_path, headless=True)
            try:
                yield browser, url, token
            finally:
                browser.close()
    finally:
        try:
            requests.post(url + '/api/desktop/shutdown', headers={'Authorization': 'Bearer ' + token}, timeout=2)
            process.wait(timeout=8)
        except (requests.RequestException, subprocess.TimeoutExpired):
            process.terminate(); process.wait(timeout=5)


def open_authenticated(browser, url, token, fallback=False):
    context = browser.new_context()
    page = context.new_page()
    external = []
    def route(request):
        if not request.request.url.startswith(url):
            external.append(request.request.url); request.abort()
        elif fallback and request.request.url.endswith('/loader.js'):
            request.abort()
        else:
            request.continue_()
    page.route('**/*', route)
    ticket = requests.post(url + '/api/desktop/ticket', headers={'Authorization': 'Bearer ' + token}, timeout=3).json()['ticket']
    page.goto(url + '/#ticket=' + ticket)
    page.wait_for_selector('#workspace', state='visible')
    return context, page, external


def test_offline_editor_upload_clear_and_empty_download(browser_host):
    browser, url, token = browser_host
    context, page, external = open_authenticated(browser, url, token)
    try:
        page.wait_for_selector('.monaco-editor')
        assert page.evaluate('location.hash') == ''
        page.locator('#fileInput').set_input_files({'name': 'input.json', 'mimeType': 'application/json', 'buffer': b'[8,9]'})
        page.wait_for_function('document.getElementById("dataset").disabled')
        page.locator('#clearUpload').click()
        assert page.locator('#dataset').is_enabled()
        page.locator('#dataset').fill('[]')
        page.locator('#runBtn').click()
        page.wait_for_selector('#download', state='visible')
        assert '0 output items' in page.locator('#preview').inner_text()
        with page.expect_download() as event:
            page.locator('#download').click()
        download = event.value
        assert Path(download.path()).read_text(encoding='utf-8') == '[]'
        assert external == []
    finally:
        context.close()


def test_plain_editor_fallback_and_unauthenticated_onboarding(browser_host):
    browser, url, token = browser_host
    context, page, external = open_authenticated(browser, url, token, fallback=True)
    try:
        assert page.locator('#code').is_visible()
        page.locator('#code').fill('def task(data): return data')
        page.locator('#dataset').fill('[1,2]')
        page.locator('#runBtn').click()
        page.wait_for_selector('#detail', state='visible')
        assert 'queued' in page.locator('#jobTitle').inner_text()
        assert external == []
    finally:
        context.close()
    context = browser.new_context()
    page = context.new_page(); page.goto(url)
    assert page.locator('#onboarding').is_visible()
    assert page.locator('#workspace').is_hidden()
    context.close()
