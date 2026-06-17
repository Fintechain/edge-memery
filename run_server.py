#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Entry point for Memory Curator MCP Server.

Run:
    python run_server.py
Or:
    python -m memory_server                 (after installation)
"""

import importlib.util
import sys
from pathlib import Path
from types import ModuleType


def _load_source_package() -> ModuleType:
    """Load this directory as memory_server when it has not been installed."""
    package_dir = Path(__file__).resolve().parent
    init_file = package_dir / "__init__.py"
    spec = importlib.util.spec_from_file_location(
        "memory_server",
        init_file,
        submodule_search_locations=[str(package_dir)],
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load memory_server from {package_dir}")
    module = importlib.util.module_from_spec(spec)
    sys.modules["memory_server"] = module
    spec.loader.exec_module(module)
    return module


_load_source_package()
from memory_server.server import main

if __name__ == "__main__":
    main()
