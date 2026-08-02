#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Web UI server for M3U8 Helper."""

from __future__ import annotations

import json
import os
import platform
import shutil
import subprocess
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources
from pathlib import Path
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
from .jobs import DownloadJobManager, JobError
from .settings import SettingsError, SettingsManager


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
        origin = self.headers.get("Origin", "")
        if self.server.settings.is_extension_origin(origin):
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")
        self.end_headers()
        self.wfile.write(body)

    def _send_json(self, status: int, payload: Dict[str, object]) -> None:
        body = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
        self._send_bytes(status, body, "application/json; charset=utf-8")

    def _read_asset(self, rel_path: str) -> bytes:
        return ASSET_ROOT.joinpath(rel_path).read_bytes()

    def _read_json(self) -> Dict[str, object] | None:
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length) if length else b"{}"
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            self._send_json(400, {"error": "無效的 JSON 格式"})
            return None
        if not isinstance(payload, dict):
            self._send_json(400, {"error": "JSON 內容必須是物件。"})
            return None
        return payload

    def _is_local_web_request(self) -> bool:
        origin = self.headers.get("Origin", "")
        if not origin:
            return True
        parsed = urlparse(origin)
        return parsed.scheme == "http" and parsed.hostname in {"127.0.0.1", "localhost"}

    def _authorized(self) -> bool:
        if self._is_local_web_request():
            return True
        origin = self.headers.get("Origin", "")
        return self.server.settings.authorize_extension(
            origin,
            self.headers.get("Authorization", ""),
        )

    def _require_authorized(self) -> bool:
        if self._authorized():
            return True
        self._send_json(401, {"error": "尚未配對，或授權權杖已失效。"})
        return False

    def do_OPTIONS(self) -> None:
        origin = self.headers.get("Origin", "")
        if not self.server.settings.is_extension_origin(origin):
            return self._send_bytes(403, b"Forbidden", "text/plain; charset=utf-8")
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", origin)
        self.send_header("Access-Control-Allow-Methods", "GET, POST, PUT, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Authorization, Content-Type")
        self.send_header("Access-Control-Max-Age", "600")
        self.send_header("Vary", "Origin")
        self.end_headers()

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path

        if path == "/api/health":
            settings = self.server.settings.public_settings()
            return self._send_json(
                200,
                {
                    "status": "ok",
                    "version": "0.2.0",
                    "paired": settings["paired"],
                    "tools": {
                        "yt_dlp": bool(shutil.which("yt-dlp")),
                        "ffmpeg": bool(shutil.which("ffmpeg")),
                    },
                },
            )

        if path == "/api/jobs":
            if not self._require_authorized():
                return
            return self._send_json(200, {"jobs": self.server.jobs.list_jobs()})

        if path.startswith("/api/jobs/"):
            if not self._require_authorized():
                return
            job_id = path.removeprefix("/api/jobs/").strip("/")
            job = self.server.jobs.get_job(job_id)
            if not job:
                return self._send_json(404, {"error": "找不到指定的下載工作。"})
            return self._send_json(200, job)

        if path == "/api/settings":
            if not self._require_authorized():
                return
            return self._send_json(200, self.server.settings.public_settings())

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
        path = parsed.path
        payload = self._read_json()
        if payload is None:
            return

        if path == "/api/pair":
            origin = self.headers.get("Origin", "")
            try:
                token = self.server.settings.pair(str(payload.get("code", "")), origin)
            except SettingsError as exc:
                return self._send_json(400, {"error": str(exc)})
            return self._send_json(200, {"status": "paired", "token": token})

        if path == "/api/pairing-code":
            if not self._is_local_web_request():
                return self._send_json(403, {"error": "只能從本機工具產生配對碼。"})
            return self._send_json(200, self.server.settings.new_pairing_code())

        if path == "/api/jobs":
            if not self._require_authorized():
                return
            curl_text = str(payload.get("curl_text", "")).strip()
            if curl_text:
                commands = split_curl_commands(curl_text)
                try:
                    index = int(payload.get("index", 1))
                except (TypeError, ValueError):
                    index = 1
                if index < 1 or index > len(commands):
                    return self._send_json(400, {"error": "cURL 索引超出範圍。"})
                url, curl_headers = parse_curl_command(commands[index - 1])
                if not url:
                    return self._send_json(400, {"error": "無法從 cURL 解析出網址。"})
                payload["url"] = url
                payload["headers"] = curl_headers
            try:
                job = self.server.jobs.create_job(payload)
            except (JobError, SettingsError) as exc:
                return self._send_json(400, {"error": str(exc)})
            return self._send_json(202, job)

        if path.startswith("/api/jobs/") and path.endswith("/cancel"):
            if not self._require_authorized():
                return
            job_id = path.removeprefix("/api/jobs/").removesuffix("/cancel").strip("/")
            job = self.server.jobs.cancel_job(job_id)
            if not job:
                return self._send_json(404, {"error": "找不到指定的下載工作。"})
            return self._send_json(200, job)

        if path == "/api/settings/open-output":
            if not self._require_authorized():
                return
            try:
                output_dir = self.server.settings.ensure_output_dir()
                open_output_directory(output_dir)
            except (SettingsError, OSError) as exc:
                return self._send_json(500, {"error": f"無法開啟下載資料夾：{exc}"})
            return self._send_json(200, {"status": "ok"})

        if path not in ("/api/parse", "/api/run"):
            return self._send_bytes(404, b"Not Found", "text/plain; charset=utf-8")
        if not self._require_authorized():
            return

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

    def do_PUT(self) -> None:
        path = urlparse(self.path).path
        if path != "/api/settings":
            return self._send_bytes(404, b"Not Found", "text/plain; charset=utf-8")
        if not self._require_authorized():
            return
        payload = self._read_json()
        if payload is None:
            return
        try:
            settings = self.server.settings.update_output_dir(str(payload.get("output_dir", "")))
        except SettingsError as exc:
            return self._send_json(400, {"error": str(exc)})
        return self._send_json(200, settings)


class M3U8HTTPServer(ThreadingHTTPServer):
    def __init__(
        self,
        server_address: tuple[str, int],
        settings: SettingsManager,
        jobs: DownloadJobManager,
    ) -> None:
        super().__init__(server_address, WebHandler)
        self.settings = settings
        self.jobs = jobs


def open_output_directory(path: Path) -> None:
    system = platform.system()
    if system == "Darwin":
        cmd = ["open", str(path)]
    elif system == "Windows":
        cmd = ["explorer", str(path)]
    else:
        cmd = ["xdg-open", str(path)]
    subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def build_server(
    host: str = "127.0.0.1",
    port: int = 8765,
    settings: SettingsManager | None = None,
    jobs: DownloadJobManager | None = None,
) -> M3U8HTTPServer:
    settings_manager = settings or SettingsManager()
    job_manager = jobs or DownloadJobManager(settings_manager)
    return M3U8HTTPServer((host, port), settings_manager, job_manager)


def run_server(host: str = "127.0.0.1", port: int = 8765) -> None:
    server = build_server(host, port)
    print(f"Web UI 已啟動：http://{host}:{port}")
    print("按 Ctrl+C 結束。")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n使用者中斷")
    finally:
        server.server_close()
        server.jobs.shutdown()


__all__ = ["M3U8HTTPServer", "WebHandler", "build_server", "run_server"]
