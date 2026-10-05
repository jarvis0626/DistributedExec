"""Developer entrypoint for the authenticated coordinator."""
import sys
from distributedexec.cli import main

if __name__ == '__main__':
    raise SystemExit(main(['host', *sys.argv[1:]]))
