#!/usr/bin/env python3
"""
stellaris-extract.py

Parses Stellaris vanilla scripts and stores the data in a structured SQLite
database (and optionally a .sql dump for review). Thin entry point — the
implementation lives in the extractor/ package (see extractor/cli.py for the
pipeline, one module per game-data domain).

Usage:
    python3 stellaris-extract.py <STELLARIS_INSTALL_DIR> [OPTIONS]

Examples:
    # Print SQL to stdout
    python3 stellaris-extract.py ~/.steam/steam/steamapps/common/Stellaris

    # Write SQL to a file
    python3 stellaris-extract.py /path/to/Stellaris --out stellaris.sql

    # Write directly to SQLite
    python3 stellaris-extract.py /path/to/Stellaris --sqlite stellaris.sqlite

    # Both at once
    python3 stellaris-extract.py /path/to/Stellaris --out stellaris.sql --sqlite stellaris.sqlite
"""

from extractor.cli import main

if __name__ == '__main__':
    main()
