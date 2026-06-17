# -*- coding: utf-8 -*-
"""Command-line entry point for Memery."""

from __future__ import annotations

import argparse
import importlib.metadata
from pathlib import Path
import sys

from .config import get_config


def _version() -> str:
    from . import __version__
    try:
        pkg_version = importlib.metadata.version("memery-mcp")
        return __version__ or pkg_version
    except importlib.metadata.PackageNotFoundError:
        return __version__


def _doctor() -> int:
    cfg = get_config()
    print(f"Memery {_version()}")
    print(f"Python: {sys.executable}")
    print(f"Database: {cfg.db_path}")
    print(f"Data directory: {cfg.data_dir}")

    try:
        from .backends.lancedb_backend import LanceDBStore
        vectors = LanceDBStore()
        print(f"Vector backend: lancedb ({vectors.count()} rows)")
    except Exception as exc:
        print(f"Vector backend: error: {exc}")
        return 1

    db_path = Path(cfg.db_path)
    print(f"Database exists: {'yes' if db_path.exists() else 'no'}")
    return 0


def main(argv: list[str] | None = None) -> int | None:
    parser = argparse.ArgumentParser(
        prog="memery",
        description="Memery MCP server and diagnostics.",
    )
    parser.add_argument(
        "--stdio",
        action="store_true",
        help="run the MCP stdio server (default when no command is given)",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"memery-mcp {_version()}",
    )
    subparsers = parser.add_subparsers(dest="command")
    subparsers.add_parser("doctor", help="check the local Memery installation")

    args = parser.parse_args(argv)

    if args.command == "doctor":
        return _doctor()

    from .server import main as server_main
    server_main()
    return None


if __name__ == "__main__":
    raise SystemExit(main())
