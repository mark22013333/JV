#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
核心功能：
- 掃描頁面找出可能的 m3u8
- 產生 ffmpeg 指令
- 基本 URL/檔名推測

注意：本工具僅供合法授權內容使用，不協助繞過 DRM/付費牆/反爬機制。
"""

from __future__ import annotations

import mimetypes
import os
import re
from typing import Callable, Dict, Iterable, List
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/130.0.0.0 Safari/537.36"
)

DEFAULT_HEADERS = {
    "User-Agent": DEFAULT_UA,
    "Accept": "*/*",
    "Accept-Language": "zh-TW,zh;q=0.9,en-US;q=0.8,en;q=0.7",
}

ProgressCallback = Callable[[str], None]


def format_size(num_bytes: int) -> str:
    value = float(num_bytes)
    units = ("B", "KB", "MB", "GB", "TB")

    for unit in units:
        if value < 1024 or unit == units[-1]:
            if unit == "B":
                return f"{int(value)} {unit}"
            return f"{value:.1f} {unit}"
        value /= 1024

    return f"{num_bytes} B"


def parse_header_kv(header: str) -> tuple[str, str]:
    if ":" not in header:
        raise ValueError("Header 必須是 'Key: Value' 格式")
    key, value = header.split(":", 1)
    key = key.strip()
    value = value.strip()
    if not key:
        raise ValueError("Header key 不可為空")
    return key, value


def build_headers(
    user_agent: str | None,
    referer: str | None,
    cookie: str | None,
    extra_headers: Iterable[str] | None,
    base_headers: Dict[str, str] | None = None,
) -> Dict[str, str]:
    headers: Dict[str, str] = dict(DEFAULT_HEADERS)
    if base_headers:
        headers.update(base_headers)
    if user_agent:
        headers["User-Agent"] = user_agent
    if referer:
        headers["Referer"] = referer
    if cookie:
        headers["Cookie"] = cookie
    if extra_headers:
        for h in extra_headers:
            key, value = parse_header_kv(h)
            headers[key] = value
    return headers


def get_page_content(url: str, headers: Dict[str, str], timeout: int) -> str:
    try:
        resp = requests.get(url, headers=headers, timeout=timeout)
        resp.raise_for_status()
        return resp.text
    except Exception as exc:
        print(f"取得頁面失敗：{exc}")
        return ""


def _normalize_url(url: str, base_url: str) -> str:
    url = url.strip()
    if not url:
        return ""
    if url.startswith("//"):
        return "https:" + url
    if url.startswith("http://") or url.startswith("https://"):
        return url
    return urljoin(base_url, url)


def find_m3u8_candidates(page_content: str, base_url: str, max_candidates: int) -> List[str]:
    candidates: List[str] = []

    def add(url: str) -> None:
        normalized = _normalize_url(url, base_url)
        if normalized:
            candidates.append(normalized)

    pattern_http = r"(https?://[^\s'\"<>]+?\.m3u8(?:\?[^'\"<>\s]*)?)"
    for match in re.findall(pattern_http, page_content):
        add(match)

    pattern_quoted = r"[\"']([^\"']+?\.m3u8[^\"']*)[\"']"
    for match in re.findall(pattern_quoted, page_content):
        add(match)

    soup = BeautifulSoup(page_content, "html.parser")

    for tag in soup.find_all(True):
        for attr in ("src", "data-src", "data-url", "data-file", "href"):
            value = tag.get(attr)
            if value and ".m3u8" in value:
                add(value)

    for script in soup.find_all("script"):
        if script.string:
            matches = re.findall(
                r"(?:src|url|file|playlist)\s*[:=]\s*[\"'](.*?\.m3u8.*?)[\"']",
                script.string,
            )
            for m in matches:
                add(m)

    seen = set()
    unique: List[str] = []
    for url in candidates:
        if url not in seen:
            seen.add(url)
            unique.append(url)

    unique.sort(key=len, reverse=True)
    return unique[:max_candidates]


def guess_output_name(url: str) -> str:
    path = urlparse(url).path
    base = os.path.basename(path) or "video"
    if base.endswith(".m3u8"):
        base = base[: -len(".m3u8")]
        base = re.sub(r"[^A-Za-z0-9._-]+", "_", base).strip("_")
        if not base:
            base = "video"
        return f"{base}.mp4"

    base = re.sub(r"[^A-Za-z0-9._-]+", "_", base).strip("_")
    if not base:
        return "download.bin"
    if "." in base:
        return base
    return f"{base}.bin"


def is_stream_url(url: str) -> bool:
    url_l = url.lower()
    return ".m3u8" in url_l or ".mpd" in url_l


def extract_title(page_content: str) -> str:
    if not page_content:
        return ""
    soup = BeautifulSoup(page_content, "html.parser")

    def clean(text: str) -> str:
        return " ".join(text.split()).strip()

    meta = soup.find("meta", property="og:title")
    if meta and meta.get("content"):
        return clean(meta["content"])

    meta = soup.find("meta", attrs={"name": "title"})
    if meta and meta.get("content"):
        return clean(meta["content"])

    if soup.title and soup.title.string:
        return clean(soup.title.string)

    h1 = soup.find("h1")
    if h1:
        text = h1.get_text(strip=True)
        if text:
            return clean(text)
    return ""


def sanitize_filename(text: str, max_len: int = 120) -> str:
    if not text:
        return "video"
    text = text.strip()
    text = re.sub(r"[<>:\"/\\\\|?*\\x00-\\x1f]", "_", text)
    text = re.sub(r"\\s+", " ", text).strip()
    if not text:
        return "video"
    if len(text) > max_len:
        text = text[:max_len].rstrip()
    return text


def make_title_based_name(title: str, url: str) -> str:
    base = sanitize_filename(title)
    ext = os.path.splitext(guess_output_name(url))[1]
    if not ext:
        ext = ".mp4" if is_stream_url(url) else ".bin"
    return f"{base}{ext}"


def build_ffmpeg_command(
    m3u8_url: str,
    headers: Dict[str, str],
    output_path: str,
    ffmpeg_path: str,
    overwrite: bool,
) -> List[str]:
    header_lines = [f"{k}: {v}" for k, v in headers.items() if v]
    header_blob = "\\r\\n".join(header_lines) + "\\r\\n" if header_lines else ""

    cmd: List[str] = [ffmpeg_path]
    if overwrite:
        cmd.append("-y")
    if header_blob:
        cmd.extend(["-headers", header_blob])
    cmd.extend(
        [
            "-i",
            m3u8_url,
            "-c",
            "copy",
            "-bsf:a",
            "aac_adtstoasc",
            "-movflags",
            "+faststart",
            output_path,
        ]
    )
    return cmd


def check_url(
    url: str,
    headers: Dict[str, str],
    timeout: int,
) -> Dict[str, object]:
    """
    Check URL availability with HEAD, fallback to GET if method unsupported.
    Returns: {"method": str, "status": int, "reason": str, "final_url": str, "headers": dict}
    """
    try:
        resp = requests.head(url, headers=headers, allow_redirects=True, timeout=timeout)
        method = "HEAD"
        if resp.status_code in (405, 501):
            resp.close()
            resp = requests.get(url, headers=headers, allow_redirects=True, timeout=timeout, stream=True)
            method = "GET"
        result = {
            "method": method,
            "status": resp.status_code,
            "reason": resp.reason,
            "final_url": resp.url,
            "headers": dict(resp.headers),
        }
        resp.close()
        return result
    except Exception as exc:
        return {
            "method": "ERROR",
            "status": 0,
            "reason": str(exc),
            "final_url": url,
            "headers": {},
        }


def download_file(
    url: str,
    headers: Dict[str, str],
    output_path: str,
    timeout: int,
    overwrite: bool,
    progress_callback: ProgressCallback | None = None,
) -> str:
    if not overwrite and os.path.exists(output_path):
        raise FileExistsError(f"檔案已存在：{output_path}")

    resp = requests.get(url, headers=headers, allow_redirects=True, timeout=timeout, stream=True)
    if resp.status_code >= 400:
        status = resp.status_code
        reason = resp.reason
        resp.close()
        raise RuntimeError(f"下載失敗：HTTP {status} {reason}")

    content_type = resp.headers.get("Content-Type", "")
    ext = ""
    if "." not in os.path.basename(output_path):
        if content_type:
            ext = mimetypes.guess_extension(content_type.split(";", 1)[0].strip()) or ""

    final_path = output_path + ext if ext else output_path
    if final_path != output_path and os.path.exists(final_path) and not overwrite:
        resp.close()
        raise FileExistsError(f"檔案已存在：{final_path}")

    content_length = int(resp.headers.get("Content-Length", "0") or 0)
    if progress_callback:
        progress_callback(f"[download] 開始下載：{final_path}")
        if resp.url != url:
            progress_callback(f"[download] 最終網址：{resp.url}")
        if content_length > 0:
            progress_callback(f"[download] 檔案大小：{format_size(content_length)}")
        else:
            progress_callback("[download] 檔案大小：未知")

    total = 0
    last_reported_percent = -1
    last_reported_bytes = 0
    with open(final_path, "wb") as f:
        for chunk in resp.iter_content(chunk_size=1024 * 1024):
            if not chunk:
                continue
            f.write(chunk)
            total += len(chunk)
            if not progress_callback:
                continue

            if content_length > 0:
                percent = min(100, int(total * 100 / content_length))
                if percent >= last_reported_percent + 10 or total >= content_length:
                    progress_callback(
                        f"[download] 進度 {percent:3d}% "
                        f"({format_size(total)} / {format_size(content_length)})"
                    )
                    last_reported_percent = percent
            elif total - last_reported_bytes >= 5 * 1024 * 1024:
                progress_callback(f"[download] 已下載 {format_size(total)}")
                last_reported_bytes = total
    resp.close()

    if progress_callback and content_length <= 0 and total != last_reported_bytes:
        progress_callback(f"[download] 已下載 {format_size(total)}")
    if progress_callback:
        progress_callback(f"[download] 下載完成：{final_path} ({format_size(total)})")

    return f"{final_path} ({total} bytes)"
