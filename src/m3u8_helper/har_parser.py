#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Parse HAR files to extract media URLs."""

from __future__ import annotations

import json
from typing import Dict, List, Tuple

STREAM_MIME = {
    "application/vnd.apple.mpegurl",
    "application/x-mpegURL",
    "application/dash+xml",
}


def load_har(path: str) -> Dict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def extract_har_entries(har_data: Dict) -> List[Dict]:
    log = har_data.get("log", {})
    entries = log.get("entries", [])
    return entries if isinstance(entries, list) else []


def is_media_mime(mime: str) -> bool:
    if not mime:
        return False
    mime = mime.split(";", 1)[0].strip().lower()
    return mime in STREAM_MIME or mime.startswith("video/") or mime.startswith("audio/")


def extract_media_candidates(har_data: Dict) -> List[Tuple[str, str]]:
    """
    Return list of (url, mimeType) that look like media resources.
    """
    entries = extract_har_entries(har_data)
    candidates: List[Tuple[str, str]] = []

    for entry in entries:
        request = entry.get("request", {})
        response = entry.get("response", {})
        content = response.get("content", {})
        url = request.get("url", "")
        mime = content.get("mimeType", "")
        if not mime:
            headers = response.get("headers", [])
            for h in headers:
                if h.get("name", "").lower() == "content-type":
                    mime = h.get("value", "")
                    break

        if not url:
            continue

        url_l = url.lower()
        if any(ext in url_l for ext in (".m3u8", ".mpd", ".mp4", ".m4s", ".ts", ".aac", ".mp3", ".webm")):
            candidates.append((url, mime))
            continue

        if is_media_mime(mime):
            candidates.append((url, mime))
            continue

    # De-duplicate, preserve order
    seen = set()
    unique: List[Tuple[str, str]] = []
    for url, mime in candidates:
        if url in seen:
            continue
        seen.add(url)
        unique.append((url, mime))

    # Sort by URL length (longer first) to surface full playlist URLs
    unique.sort(key=lambda x: len(x[0]), reverse=True)
    return unique
