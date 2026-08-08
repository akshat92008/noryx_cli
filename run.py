#!/usr/bin/env python3
"""Source-checkout compatibility entrypoint for Nexus CLI.

Production users should use the installed ``nexus`` console script.  This file
exists so source archives, smoke tests, and contributors have one explicit,
non-duplicated entry path.
"""
from nexus.cli import main


if __name__ == "__main__":
    main()
