"""Entry point: python main.py URL [options]. The CLI lives in agent_access/cli.py."""

import sys

from agent_access.cli import main

if __name__ == "__main__":
    sys.exit(main())
