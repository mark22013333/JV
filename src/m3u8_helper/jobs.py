"""Thread-safe background download queue used by the local Web service."""

from __future__ import annotations

import json
import queue
import re
import shutil
import subprocess
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Dict, List
from urllib.parse import urlparse, urlunparse

import requests

from .core import (
    build_ffmpeg_command,
    build_headers,
    build_yt_dlp_command,
    guess_output_name,
    is_stream_url,
    resolve_stream_variant,
    sanitize_filename,
)
from .settings import SettingsManager


ACTIVE_STATES = {"queued", "resolving", "downloading", "merging"}
FINAL_STATES = {"completed", "failed", "cancelled"}
ALLOWED_HEADERS = {"user-agent", "referer", "origin", "cookie", "accept", "accept-language"}


def redact_url(url: str) -> str:
    parsed = urlparse(url)
    return urlunparse((parsed.scheme, parsed.netloc, parsed.path, "", "", ""))


def parse_progress_line(line: str) -> Dict[str, object]:
    result: Dict[str, object] = {}
    percent_match = re.search(r"(?:\[download\]\s+|進度\s+)(\d{1,3}(?:\.\d+)?)%", line)
    if percent_match:
        result["progress"] = max(0.0, min(100.0, float(percent_match.group(1))))
    speed_match = re.search(r"\bat\s+([^\s]+/s)", line)
    if not speed_match:
        speed_match = re.search(r"\bspeed=\s*([^\s]+)", line)
    if speed_match:
        result["speed"] = speed_match.group(1)
    eta_match = re.search(r"\bETA\s+([0-9:]+)", line)
    if eta_match:
        result["eta"] = eta_match.group(1)
    lowered = line.lower()
    if "merg" in lowered or "fixup" in lowered or "movflags" in lowered:
        result["status"] = "merging"
    elif "download" in lowered or "time=" in lowered:
        result["status"] = "downloading"
    return result


def safe_output_name(url: str, requested_name: str) -> str:
    guessed = guess_output_name(url)
    guessed_ext = Path(guessed).suffix or (".mp4" if is_stream_url(url) else ".bin")
    if requested_name.strip():
        raw_name = Path(requested_name.strip()).name
        cleaned = sanitize_filename(raw_name)
        if not Path(cleaned).suffix:
            cleaned += guessed_ext
    else:
        cleaned = sanitize_filename(guessed)
    stem = Path(cleaned).stem or "video"
    suffix = Path(cleaned).suffix or guessed_ext
    timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
    return f"{stem}-{timestamp}{suffix}"


def unique_output_path(output_dir: Path, output_name: str) -> Path:
    candidate = output_dir / output_name
    if not candidate.exists():
        return candidate
    stem = candidate.stem
    suffix = candidate.suffix
    for index in range(2, 10_000):
        numbered = output_dir / f"{stem}-{index}{suffix}"
        if not numbered.exists():
            return numbered
    raise JobError("無法建立不重複的輸出檔名。")


def normalize_headers(raw_headers: object) -> Dict[str, str]:
    if not isinstance(raw_headers, dict):
        return build_headers(None, None, None, None)
    filtered: Dict[str, str] = {}
    for key, value in raw_headers.items():
        if not isinstance(key, str) or not isinstance(value, str):
            continue
        if (
            key.lower() in ALLOWED_HEADERS
            and "\r" not in key
            and "\n" not in key
            and "\r" not in value
            and "\n" not in value
        ):
            filtered[key] = value
    return build_headers(None, None, None, None, base_headers=filtered)


@dataclass
class DownloadJob:
    id: str
    source_url: str
    headers: Dict[str, str]
    requested_name: str = ""
    quality: str = "best"
    page_title: str = ""
    status: str = "queued"
    progress: float | None = 0.0
    speed: str = ""
    eta: str = ""
    output: str = ""
    error: str = ""
    downloader: str = ""
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    _process: subprocess.Popen[str] | None = field(default=None, repr=False)
    _cancel_requested: bool = field(default=False, repr=False)

    def public_dict(self) -> Dict[str, object]:
        return {
            "id": self.id,
            "source": redact_url(self.source_url),
            "status": self.status,
            "progress": self.progress,
            "speed": self.speed,
            "eta": self.eta,
            "output": self.output,
            "error": self.error,
            "downloader": self.downloader,
            "quality": self.quality,
            "page_title": self.page_title,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


class JobError(ValueError):
    pass


class DownloadJobManager:
    def __init__(
        self,
        settings: SettingsManager,
        history_path: Path | None = None,
        start_worker: bool = True,
    ) -> None:
        self.settings = settings
        self.history_path = history_path or settings.config_path.with_name("jobs.json")
        self._jobs: Dict[str, DownloadJob] = {}
        self._lock = threading.RLock()
        self._queue: queue.Queue[str | None] = queue.Queue()
        self._stopped = threading.Event()
        self._load_history()
        self._worker: threading.Thread | None = None
        if start_worker:
            self._worker = threading.Thread(target=self._worker_loop, name="download-worker", daemon=True)
            self._worker.start()

    def _load_history(self) -> None:
        try:
            rows = json.loads(self.history_path.read_text(encoding="utf-8"))
        except (FileNotFoundError, OSError, json.JSONDecodeError):
            return
        if not isinstance(rows, list):
            return
        for row in rows[-100:]:
            if not isinstance(row, dict) or not isinstance(row.get("id"), str):
                continue
            status = str(row.get("status", "failed"))
            error = str(row.get("error", ""))
            if status in ACTIVE_STATES:
                status = "failed"
                error = "本機服務重新啟動，先前工作已中斷。"
            job = DownloadJob(
                id=row["id"],
                source_url=str(row.get("source", "")),
                headers={},
                quality=str(row.get("quality", "best")),
                page_title=str(row.get("page_title", "")),
                status=status,
                progress=row.get("progress") if isinstance(row.get("progress"), (int, float)) else None,
                speed=str(row.get("speed", "")),
                eta=str(row.get("eta", "")),
                output=str(row.get("output", "")),
                error=error,
                downloader=str(row.get("downloader", "")),
                created_at=float(row.get("created_at", time.time())),
                updated_at=time.time(),
            )
            self._jobs[job.id] = job

    def _persist(self) -> None:
        try:
            self.history_path.parent.mkdir(parents=True, exist_ok=True)
            rows = [job.public_dict() for job in self._jobs.values()][-100:]
            temp_path = self.history_path.with_suffix(".tmp")
            temp_path.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
            temp_path.replace(self.history_path)
        except OSError:
            pass

    def _set(self, job: DownloadJob, **changes: object) -> None:
        with self._lock:
            for key, value in changes.items():
                setattr(job, key, value)
            job.updated_at = time.time()
            self._persist()

    def create_job(self, payload: Dict[str, object]) -> Dict[str, object]:
        source_url = str(payload.get("url", "")).strip()
        parsed = urlparse(source_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise JobError("下載網址必須是有效的 HTTP 或 HTTPS 網址。")
        if not (shutil.which("yt-dlp") or shutil.which("ffmpeg")) and is_stream_url(source_url):
            raise JobError("找不到 yt-dlp 或 ffmpeg，請先安裝其中一項工具。")
        quality = str(payload.get("quality", "best")).strip() or "best"
        if quality not in {"best", "worst", "1080p", "720p", "480p"}:
            raise JobError("不支援指定的畫質。")
        job = DownloadJob(
            id=uuid.uuid4().hex,
            source_url=source_url,
            headers=normalize_headers(payload.get("headers")),
            requested_name=str(payload.get("filename", "")),
            quality=quality,
            page_title=str(payload.get("page_title", ""))[:200],
        )
        with self._lock:
            self._jobs[job.id] = job
            self._persist()
        self._queue.put(job.id)
        return job.public_dict()

    def list_jobs(self) -> List[Dict[str, object]]:
        with self._lock:
            jobs = sorted(self._jobs.values(), key=lambda item: item.created_at, reverse=True)
            return [job.public_dict() for job in jobs]

    def get_job(self, job_id: str) -> Dict[str, object] | None:
        with self._lock:
            job = self._jobs.get(job_id)
            return job.public_dict() if job else None

    def cancel_job(self, job_id: str) -> Dict[str, object] | None:
        with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                return None
            if job.status in FINAL_STATES:
                return job.public_dict()
            job._cancel_requested = True
            process = job._process
            if job.status == "queued":
                self._set(job, status="cancelled", error="已由使用者取消。")
            elif process and process.poll() is None:
                process.terminate()
            return job.public_dict()

    def _worker_loop(self) -> None:
        while not self._stopped.is_set():
            job_id = self._queue.get()
            if job_id is None:
                self._queue.task_done()
                break
            with self._lock:
                job = self._jobs.get(job_id)
            if job and job.status == "queued" and not job._cancel_requested:
                self._run_job(job)
            self._queue.task_done()

    def _run_job(self, job: DownloadJob) -> None:
        try:
            self._set(job, status="resolving", progress=0.0, error="")
            output_dir = self.settings.ensure_output_dir().resolve()
            resolved_url = job.source_url
            if ".m3u8" in job.source_url.lower():
                stream = resolve_stream_variant(job.source_url, job.headers, 15, job.quality)
                resolved_url = str(stream.get("url", job.source_url))
                if stream.get("quality") and stream.get("quality") != "source":
                    job.quality = str(stream["quality"])
            output_name = safe_output_name(resolved_url, job.requested_name or job.page_title)
            output_path = unique_output_path(output_dir, output_name).resolve()
            if output_dir not in output_path.parents:
                raise JobError("輸出檔名超出下載資料夾範圍。")
            self._set(job, output=str(output_path), status="downloading")
            if is_stream_url(resolved_url):
                self._run_stream(job, resolved_url, output_path)
            else:
                self._run_direct(job, resolved_url, output_path)
            if job._cancel_requested:
                for partial in (output_path, Path(f"{output_path}.part")):
                    try:
                        partial.unlink()
                    except OSError:
                        pass
                self._set(job, status="cancelled", error="已由使用者取消。")
            else:
                self._set(job, status="completed", progress=100.0, speed="", eta="")
        except Exception as exc:
            status = "cancelled" if job._cancel_requested else "failed"
            message = "已由使用者取消。" if job._cancel_requested else str(exc)
            self._set(job, status=status, error=message, speed="", eta="")

    def _run_stream(self, job: DownloadJob, url: str, output_path: Path) -> None:
        if shutil.which("yt-dlp"):
            job.downloader = "yt-dlp"
            cmd = build_yt_dlp_command(url, job.headers, str(output_path), "yt-dlp")
            cmd[1:1] = ["--newline", "--progress"]
        elif shutil.which("ffmpeg"):
            job.downloader = "ffmpeg"
            cmd = build_ffmpeg_command(url, job.headers, str(output_path), "ffmpeg", overwrite=False)
            cmd[1:1] = ["-progress", "pipe:1", "-nostats"]
        else:
            raise JobError("找不到 yt-dlp 或 ffmpeg。")
        self._set(job, downloader=job.downloader)
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        job._process = process
        assert process.stdout is not None
        saw_forbidden = False
        saw_not_found = False
        for raw_line in process.stdout:
            lowered_line = raw_line.lower()
            saw_forbidden = saw_forbidden or "403" in lowered_line or "forbidden" in lowered_line
            saw_not_found = saw_not_found or "404" in lowered_line or "not found" in lowered_line
            if job._cancel_requested and process.poll() is None:
                process.terminate()
            changes = parse_progress_line(raw_line.strip())
            if changes:
                self._set(job, **changes)
        return_code = process.wait()
        job._process = None
        if job._cancel_requested:
            return
        if return_code != 0:
            if saw_forbidden:
                raise RuntimeError("來源拒絕存取（HTTP 403）。請重新播放影片後再試，讓擴充功能取得最新授權資訊。")
            if saw_not_found:
                raise RuntimeError("找不到媒體來源（HTTP 404），網址可能已過期。請重新播放影片後再試。")
            raise RuntimeError(f"{job.downloader} 執行失敗（結束碼 {return_code}）。")

    def _run_direct(self, job: DownloadJob, url: str, output_path: Path) -> None:
        job.downloader = "requests"
        self._set(job, downloader="requests")
        with requests.get(url, headers=job.headers, allow_redirects=True, timeout=30, stream=True) as response:
            if response.status_code >= 400:
                raise RuntimeError(f"下載失敗：HTTP {response.status_code} {response.reason}")
            total_size = int(response.headers.get("Content-Length", "0") or 0)
            downloaded = 0
            with output_path.open("xb") as output_file:
                for chunk in response.iter_content(chunk_size=1024 * 1024):
                    if job._cancel_requested:
                        break
                    if not chunk:
                        continue
                    output_file.write(chunk)
                    downloaded += len(chunk)
                    progress = (downloaded * 100.0 / total_size) if total_size else None
                    self._set(job, progress=progress)
        if job._cancel_requested:
            try:
                output_path.unlink()
            except OSError:
                pass

    def wait(self, timeout: float | None = None) -> bool:
        started = time.monotonic()
        while self._queue.unfinished_tasks:
            if timeout is not None and time.monotonic() - started >= timeout:
                return False
            time.sleep(0.01)
        return True

    def shutdown(self) -> None:
        self._stopped.set()
        self._queue.put(None)
        if self._worker:
            self._worker.join(timeout=2)


__all__ = [
    "ACTIVE_STATES",
    "DownloadJob",
    "DownloadJobManager",
    "JobError",
    "normalize_headers",
    "parse_progress_line",
    "redact_url",
    "safe_output_name",
    "unique_output_path",
]
