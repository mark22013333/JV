#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""IDE-friendly entrypoint for the M3U8 Helper Web UI."""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "src")

# Avoid shadowing the package with the local m3u8_helper.py shim.
sys.path = [p for p in sys.path if os.path.abspath(p or ".") != HERE]
sys.path.insert(0, SRC)

from m3u8_helper.web_app import run_server  # noqa: E402


def main() -> None:
    # Keep defaults simple for IDE run.
    run_server()


if __name__ == "__main__":
    main()
