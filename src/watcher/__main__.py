"""Entry point for the grading system.

Usage:
    python -m src.watcher              # Watch inbox/ continuously
    python -m src.watcher --once       # Process existing files and exit
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from src.watcher import start


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Automated assignment grading — file watcher"
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Process existing inbox files and exit (no continuous watching)",
    )
    args = parser.parse_args()
    start(once=args.once)


if __name__ == "__main__":
    main()
