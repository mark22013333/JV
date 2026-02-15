#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Parse curl commands for URL and headers."""

from __future__ import annotations

import shlex
from typing import Dict, Tuple


def parse_curl_command(curl_cmd: str) -> Tuple[str, Dict[str, str]]:
    """
    Parse a curl command string and return (url, headers).

    Supported flags:
    -H/--header, -A/--user-agent, -e/--referer/--referrer, -b/--cookie, --url
    """
    if not curl_cmd.strip():
        return "", {}

    normalized = curl_cmd.replace("\\\r\n", " ").replace("\\\n", " ")
    normalized = normalized.strip()
    if normalized.endswith("\\"):
        normalized = normalized[:-1].rstrip()
    tokens = shlex.split(normalized, posix=True)
    if not tokens:
        return "", {}

    if "curl" in tokens:
        start = tokens.index("curl") + 1
        tokens = tokens[start:]

    url = ""
    headers: Dict[str, str] = {}

    i = 0
    while i < len(tokens):
        tok = tokens[i]
        if tok in ("-H", "--header") and i + 1 < len(tokens):
            header = tokens[i + 1]
            if ":" in header:
                key, value = header.split(":", 1)
                headers[key.strip()] = value.strip()
            i += 2
            continue
        if tok in ("-A", "--user-agent") and i + 1 < len(tokens):
            headers["User-Agent"] = tokens[i + 1]
            i += 2
            continue
        if tok in ("-e", "--referer", "--referrer") and i + 1 < len(tokens):
            headers["Referer"] = tokens[i + 1]
            i += 2
            continue
        if tok in ("-b", "--cookie") and i + 1 < len(tokens):
            headers["Cookie"] = tokens[i + 1]
            i += 2
            continue
        if tok == "--url" and i + 1 < len(tokens):
            url = tokens[i + 1]
            i += 2
            continue

        if tok.startswith("http://") or tok.startswith("https://"):
            if not url:
                url = tok
        i += 1

    return url, headers
