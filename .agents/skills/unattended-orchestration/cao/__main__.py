"""Entry point so ``python -m cao <verb>`` works.

Without this file the package is importable but not runnable, and the command
every doc in this lane prints fails with "cannot be directly executed".
"""

from cao.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
