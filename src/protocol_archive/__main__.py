from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import List, Optional

from . import APP_NAME, __version__
from .config import default_db_path


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="protocol-archive", description=APP_NAME)
    parser.add_argument("--db", type=Path, default=None,
                        help="путь к файлу базы SQLite (по умолчанию ~/.local/share/protocol-archive/archive.sqlite3)")
    parser.add_argument("--check", action="store_true",
                        help="проверить зависимости и выйти (без запуска окна)")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    args = parser.parse_args(argv)

    if args.check:
        from .envcheck import main as check_main
        return check_main()

    from .ui.app import run
    return run(args.db or default_db_path())


if __name__ == "__main__":
    sys.exit(main())
