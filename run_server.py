#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Entry point for Memory Curator MCP Server.

Run:
    python run_server.py
Or:
    python -m memory_server                 (after installation)
"""

import sys
from pathlib import Path

# Make the repository's parent importable for direct source-tree execution.
_parent = Path(__file__).resolve().parent.parent
if str(_parent) not in sys.path:
    sys.path.insert(0, str(_parent))

from memory_server.server import main

if __name__ == "__main__":
    main()
