"""Developer entrypoint; pair in the launcher to create a worker profile."""
import sys
from distributedexec.cli import main

if __name__ == '__main__':
    raise SystemExit(main(['worker', *sys.argv[1:]]))
