import json
import time

import pytest

from m3u8_helper.jobs import (
    DownloadJob,
    DownloadJobManager,
    JobError,
    normalize_headers,
    parse_progress_line,
    redact_url,
    safe_output_name,
    unique_output_path,
)
from m3u8_helper.settings import SettingsManager


def test_parse_yt_dlp_progress_line():
    parsed = parse_progress_line("[download]  42.3% of 10.00MiB at 2.15MiB/s ETA 00:03")
    assert parsed == {
        "progress": 42.3,
        "speed": "2.15MiB/s",
        "eta": "00:03",
        "status": "downloading",
    }


def test_parse_ffmpeg_progress_line():
    parsed = parse_progress_line("frame=1 time=00:00:02.00 speed=1.25x")
    assert parsed["speed"] == "1.25x"
    assert parsed["status"] == "downloading"


def test_safe_output_name_strips_path_and_adds_timestamp():
    name = safe_output_name("https://cdn.example.com/master.m3u8", "../../我的影片")
    assert "/" not in name
    assert ".." not in name
    assert name.startswith("我的影片-")
    assert name.endswith(".mp4")


def test_unique_output_path_adds_sequence_for_collision(tmp_path):
    first = tmp_path / "video-20260717120000.mp4"
    first.touch()
    assert unique_output_path(tmp_path, first.name).name == "video-20260717120000-2.mp4"


def test_redact_url_removes_query_and_fragment():
    assert redact_url("https://cdn.example.com/v.m3u8?token=secret#x") == "https://cdn.example.com/v.m3u8"


def test_normalize_headers_filters_unknown_and_injected_headers():
    headers = normalize_headers(
        {
            "Referer": "https://example.com/watch",
            "Cookie": "sid=abc",
            "Authorization": "Bearer secret",
            "X-Test": "bad\r\nInjected: yes",
        }
    )
    assert headers["Referer"] == "https://example.com/watch"
    assert headers["Cookie"] == "sid=abc"
    assert "Authorization" not in headers
    assert "X-Test" not in headers


def test_create_job_rejects_invalid_url(tmp_path):
    settings = SettingsManager(tmp_path / "settings.json")
    manager = DownloadJobManager(settings, tmp_path / "jobs.json", start_worker=False)
    with pytest.raises(JobError, match="HTTP"):
        manager.create_job({"url": "file:///tmp/video.mp4"})


def test_cancel_queued_job(tmp_path):
    settings = SettingsManager(tmp_path / "settings.json")
    manager = DownloadJobManager(settings, tmp_path / "jobs.json", start_worker=False)
    created = manager.create_job({"url": "https://example.com/video.mp4"})

    cancelled = manager.cancel_job(created["id"])

    assert cancelled["status"] == "cancelled"
    assert "使用者取消" in cancelled["error"]


def test_run_stream_parses_mocked_process_progress(tmp_path, monkeypatch):
    class FakeProcess:
        stdout = iter(["[download]  51.0% of 10MiB at 2MiB/s ETA 00:03\n"])

        def wait(self):
            return 0

        def poll(self):
            return None

        def terminate(self):
            return None

    settings = SettingsManager(tmp_path / "settings.json")
    manager = DownloadJobManager(settings, tmp_path / "jobs.json", start_worker=False)
    job = DownloadJob("job-1", "https://example.com/master.m3u8", {})
    monkeypatch.setattr("m3u8_helper.jobs.shutil.which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr("m3u8_helper.jobs.subprocess.Popen", lambda *args, **kwargs: FakeProcess())

    manager._run_stream(job, job.source_url, tmp_path / "video.mp4")

    assert job.downloader == "yt-dlp"
    assert job.progress == 51.0
    assert job.speed == "2MiB/s"
    assert job.eta == "00:03"


def test_load_history_marks_active_jobs_as_interrupted(tmp_path):
    history = tmp_path / "jobs.json"
    history.write_text(
        json.dumps(
            [
                {
                    "id": "job-1",
                    "source": "https://example.com/video.mp4",
                    "status": "downloading",
                    "created_at": time.time(),
                }
            ]
        ),
        encoding="utf-8",
    )
    manager = DownloadJobManager(
        SettingsManager(tmp_path / "settings.json"),
        history,
        start_worker=False,
    )

    job = manager.get_job("job-1")
    assert job["status"] == "failed"
    assert "重新啟動" in job["error"]
