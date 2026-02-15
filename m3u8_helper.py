#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Backward-compatible shim for running the CLI directly."""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "src")

# Remove the script directory from import path to avoid shadowing the package.
sys.path = [p for p in sys.path if os.path.abspath(p or ".") != HERE]
sys.path.insert(0, SRC)

from m3u8_helper.cli import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
