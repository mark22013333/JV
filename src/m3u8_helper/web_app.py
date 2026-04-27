#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Web UI server for M3U8 Helper."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources
from typing import Dict, List
from urllib.parse import urlparse

from .cli import add_timestamp_to_path, format_command_multiline
from .core import (
    build_ffmpeg_command,
    build_headers,
    build_yt_dlp_command,
    guess_output_name,
    is_stream_url,
    resolve_stream_variant,
    sanitize_filename,
)
from .curl_parser import parse_curl_command


@dataclass
class ParseItem:
    index: int
    input_text: str
    source_url: str
    url: str
    output: str
    command: str
    kind: str
    selected_quality: str = "source"
    is_master: bool = False
    variants: List[Dict[str, object]] | None = None
    error: str = ""


ASSET_ROOT = resources.files("m3u8_helper").joinpath("web_assets")
CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
}
STREAM_RESOLVE_TIMEOUT = 15


def split_curl_commands(text: str) -> List[str]:
    stripped = text.strip()
    if not stripped:
        return []

    lines = stripped.splitlines()
    commands: List[str] = []
    current: List[str] = []

    for line in lines:
        if not line.strip():
            if current:
                commands.append("\n".join(current).strip())
                current = []
            continue

        is_new_curl = line.lstrip().startswith("curl ")
        if is_new_curl and current:
            commands.append("\n".join(current).strip())
            current = [line]
        else:
            current.append(line)

    if current:
        commands.append("\n".join(current).strip())

    return [cmd for cmd in commands if cmd]


def _apply_index(name: str, index: int, total: int) -> str:
    if total <= 1:
        return name
    base, ext = os.path.splitext(name)
    if ext:
        return f"{base}-{index}{ext}"
    return f"{name}-{index}"


def derive_output_name(url: str, custom_name: str | None, index: int, total: int) -> str:
    default_name = guess_output_name(url)
    default_ext = os.path.splitext(default_name)[1]
    fallback_ext = default_ext or (".mp4" if is_stream_url(url) else ".bin")

    if custom_name:
        base = sanitize_filename(custom_name.strip())
        if not base:
            base = "video"
        base = _apply_index(base, index, total)
        root, ext = os.path.splitext(base)
        if not ext:
            base = f"{base}{fallback_ext}"
        output_name = base
    else:
        output_name = _apply_index(default_name, index, total)

    return add_timestamp_to_path(output_name)


def choose_stream_downloader() -> str:
    return "yt-dlp" if shutil.which("yt-dlp") else "ffmpeg"


def resolve_stream_url_for_download(url: str, headers: Dict[str, str], quality: str) -> Dict[str, object]:
    if ".m3u8" not in url.lower():
        return {"url": url, "selected_quality": "source", "is_master": False, "variants": []}
    stream_info = resolve_stream_variant(url, headers, STREAM_RESOLVE_TIMEOUT, quality)
    return {
        "url": str(stream_info.get("url", url)),
        "selected_quality": str(stream_info.get("quality", "source")),
        "is_master": bool(stream_info.get("is_master")),
        "variants": list(stream_info.get("variants", [])),
    }


def build_download_command(url: str, headers: Dict[str, str], output_path: str) -> str:
    cmd = build_download_command_list(url, headers, output_path)
    return format_command_multiline(cmd)


def build_download_command_list(url: str, headers: Dict[str, str], output_path: str) -> List[str]:
    cmd: List[str] = ["curl", "-L", url]
    for k, v in headers.items():
        cmd.extend(["-H", f"{k}: {v}"])
    cmd.extend(["-o", output_path])
    return cmd


def parse_requests(curl_text: str, custom_name: str | None, quality: str = "best") -> List[ParseItem]:
    commands = split_curl_commands(curl_text)
    total = len(commands)
    results: List[ParseItem] = []

    for idx, cmd in enumerate(commands, 1):
        url, curl_headers = parse_curl_command(cmd)
        if not url:
            results.append(
                ParseItem(
                    index=idx,
                    input_text=cmd,
                    source_url="",
                    url="",
                    output="",
                    command="",
                    kind="",
                    error="無法從此 curl 解析出 URL。",
                )
            )
            continue

        headers = build_headers(None, None, None, None, base_headers=curl_headers)
        stream_info = resolve_stream_url_for_download(url, headers, quality)
        resolved_url = str(stream_info["url"])
        output_path = derive_output_name(resolved_url, custom_name, idx, total)

        if is_stream_url(url):
            kind = choose_stream_downloader()
            if kind == "yt-dlp":
                stream_cmd = build_yt_dlp_command(resolved_url, headers, output_path, "yt-dlp")
            else:
                stream_cmd = build_ffmpeg_command(
                    resolved_url,
                    headers,
                    output_path,
                    "ffmpeg",
                    overwrite=False,
                )
            command = format_command_multiline(stream_cmd)
        else:
            command = build_download_command(url, headers, output_path)
            kind = "curl"

        results.append(
                ParseItem(
                    index=idx,
                    input_text=cmd,
                    source_url=url,
                    url=resolved_url,
                    output=output_path,
                    command=command,
                    kind=kind,
                    selected_quality=str(stream_info["selected_quality"]),
                    is_master=bool(stream_info["is_master"]),
                    variants=list(stream_info.get("variants", [])),
                )
        )

    return results


def run_single_request(curl_cmd: str, custom_name: str | None, index: int, total: int, quality: str = "best") -> dict:
    url, curl_headers = parse_curl_command(curl_cmd)
    if not url:
        return {"status": "error", "message": "無法從此 curl 解析出 URL。"}

    headers = build_headers(None, None, None, None, base_headers=curl_headers)
    stream_info = resolve_stream_url_for_download(url, headers, quality)
    resolved_url = str(stream_info["url"])
    output_path = derive_output_name(resolved_url, custom_name, index, total)

    if is_stream_url(url):
        kind = choose_stream_downloader()
        if kind == "yt-dlp":
            cmd = build_yt_dlp_command(resolved_url, headers, output_path, "yt-dlp")
        else:
            cmd = build_ffmpeg_command(resolved_url, headers, output_path, "ffmpeg", overwrite=False)
    else:
        cmd = build_download_command_list(resolved_url, headers, output_path)
        kind = "curl"

    log_prefix = f"[download {index}/{total}]"
    print(f"{log_prefix} 開始 {kind}：{resolved_url} -> {output_path}", flush=True)

    try:
        subprocess.run(
            cmd,
            check=True,
        )
    except FileNotFoundError:
        return {
            "status": "error",
            "message": f"找不到 {kind}，請確認已安裝並在 PATH 內。",
            "command": format_command_multiline(cmd),
        }
    except subprocess.CalledProcessError as exc:
        print(f"{log_prefix} 失敗：return code={exc.returncode}", flush=True)
        return {
            "status": "error",
            "message": f"{kind} 執行失敗 (return code={exc.returncode})。",
            "command": format_command_multiline(cmd),
            "log": f"{log_prefix} 失敗：return code={exc.returncode}",
        }

    print(f"{log_prefix} 完成：{output_path}", flush=True)
    return {
        "status": "ok",
        "message": "下載完成",
        "output": output_path,
        "command": format_command_multiline(cmd),
        "selected_quality": str(stream_info["selected_quality"]),
        "is_master": bool(stream_info["is_master"]),
        "variants": list(stream_info.get("variants", [])),
        "log": f"{log_prefix} 完成：{output_path}",
    }


class WebHandler(BaseHTTPRequestHandler):
    server_version = "M3U8HelperWeb/1.0"

    def log_message(self, format: str, *args: object) -> None:
        return

    def _send_bytes(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_json(self, status: int, payload: Dict[str, object]) -> None:
        body = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
        self._send_bytes(status, body, "application/json; charset=utf-8")

    def _read_asset(self, rel_path: str) -> bytes:
        return ASSET_ROOT.joinpath(rel_path).read_bytes()

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path

        if path in ("/", "/index.html"):
            body = self._read_asset("index.html")
            return self._send_bytes(200, body, CONTENT_TYPES[".html"])

        if path.startswith("/assets/"):
            rel = path[len("/assets/") :]
            ext = os.path.splitext(rel)[1]
            content_type = CONTENT_TYPES.get(ext, "application/octet-stream")
            try:
                body = self._read_asset(rel)
            except FileNotFoundError:
                return self._send_bytes(404, b"Not Found", "text/plain; charset=utf-8")
            return self._send_bytes(200, body, content_type)

        return self._send_bytes(404, b"Not Found", "text/plain; charset=utf-8")

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path not in ("/api/parse", "/api/run"):
            return self._send_bytes(404, b"Not Found", "text/plain; charset=utf-8")

        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length) if length else b"{}"
        try:
            payload = json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError:
            return self._send_json(400, {"error": "無效的 JSON 格式"})

        curl_text = str(payload.get("curl_text", ""))
        custom_name = str(payload.get("custom_name", "")).strip() or None
        quality = str(payload.get("quality", "best")).strip() or "best"

        if parsed.path == "/api/parse":
            items = parse_requests(curl_text, custom_name, quality)
            response = {
                "count": len(items),
                "items": [item.__dict__ for item in items],
            }
            return self._send_json(200, response)

        # /api/run
        commands = split_curl_commands(curl_text)
        if not commands:
            return self._send_json(400, {"status": "error", "message": "沒有可執行的 curl。"})

        try:
            index = int(payload.get("index", 1))
        except (TypeError, ValueError):
            index = 1
        if index < 1 or index > len(commands):
            return self._send_json(400, {"status": "error", "message": "索引超出範圍。"})

        result = run_single_request(commands[index - 1], custom_name, index, len(commands), quality)
        return self._send_json(200, result)


def run_server(host: str = "127.0.0.1", port: int = 8765) -> None:
    server = ThreadingHTTPServer((host, port), WebHandler)
    print(f"Web UI 已啟動：http://{host}:{port}")
    print("按 Ctrl+C 結束。")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n使用者中斷")
    finally:
        server.server_close()


__all__ = ["run_server"]
