from m3u8_helper import core
from m3u8_helper.core import (
    build_ffmpeg_command,
    build_headers,
    build_yt_dlp_command,
    extract_title,
    find_m3u8_candidates,
    guess_output_name,
    make_title_based_name,
    parse_m3u8_variants,
    resolve_stream_variant,
    select_m3u8_variant,
)


class DummyResponse:
    def __init__(self, chunks, headers=None, status_code=200, url="https://example.com/file.bin", reason="OK"):
        self._chunks = chunks
        self.headers = headers or {}
        self.status_code = status_code
        self.url = url
        self.reason = reason
        self.closed = False

    def iter_content(self, chunk_size=0):
        yield from self._chunks

    def close(self):
        self.closed = True


def test_find_m3u8_candidates_basic():
    html = """
    <html>
      <script>
        var src = "https://example.com/video/main.m3u8?token=abc";
      </script>
    </html>
    """
    urls = find_m3u8_candidates(html, "https://example.com", 10)
    assert any(".m3u8" in u for u in urls)


def test_guess_output_name():
    url = "https://example.com/path/clip.m3u8?token=abc"
    assert guess_output_name(url) == "clip.mp4"


def test_guess_output_name_non_m3u8():
    url = "https://example.com/assets/image.jpg"
    assert guess_output_name(url) == "image.jpg"


def test_find_relative_m3u8():
    html = "<a href=\"/media/playlist.m3u8\">video</a>"
    urls = find_m3u8_candidates(html, "https://example.com/watch", 10)
    assert "https://example.com/media/playlist.m3u8" in urls


def test_extract_title_og():
    html = "<meta property='og:title' content='Hello World'>"
    assert extract_title(html) == "Hello World"


def test_make_title_based_name():
    assert make_title_based_name("Hello World", "https://example.com/a.m3u8") == "Hello World.mp4"


def test_build_headers_adds_origin_from_referer():
    headers = build_headers(None, "https://example.com/watch?id=1", None, None)
    assert headers["Origin"] == "https://example.com"


def test_build_ffmpeg_command_uses_dedicated_browser_flags():
    headers = build_headers("UA-1", "https://example.com/watch", "sid=abc", ["X-Test: yes"])
    cmd = build_ffmpeg_command(
        "https://cdn.example.com/master.m3u8",
        headers,
        "video.mp4",
        "ffmpeg",
        overwrite=False,
    )

    assert cmd[:5] == ["ffmpeg", "-user_agent", "UA-1", "-referer", "https://example.com/watch"]
    assert "-headers" in cmd
    header_blob = cmd[cmd.index("-headers") + 1]
    assert "Cookie: sid=abc" in header_blob
    assert "X-Test: yes" in header_blob
    assert "User-Agent: UA-1" not in header_blob
    assert "Referer: https://example.com/watch" not in header_blob
    assert "-allowed_extensions" in cmd


def test_build_yt_dlp_command_for_browser_like_requests():
    headers = build_headers("UA-1", "https://example.com/watch", "sid=abc", ["X-Test: yes"])
    cmd = build_yt_dlp_command(
        "https://cdn.example.com/master.m3u8",
        headers,
        "video.mp4",
        "yt-dlp",
        cookies_from_browser="chrome",
    )

    assert cmd[:6] == ["yt-dlp", "--no-part", "-o", "video.mp4", "--user-agent", "UA-1"]
    assert "--referer" in cmd
    assert "--cookies-from-browser" in cmd
    assert "chrome" in cmd
    assert "--add-header" in cmd
    assert "Cookie: sid=abc" in cmd
    assert "X-Test: yes" in cmd


def test_parse_m3u8_variants_reads_master_playlist():
    playlist = """
#EXTM3U
#EXT-X-STREAM-INF:BANDWIDTH=800000,RESOLUTION=640x360
low/index.m3u8
#EXT-X-STREAM-INF:BANDWIDTH=2800000,RESOLUTION=1920x1080
hi/index.m3u8
"""
    variants = parse_m3u8_variants(playlist, "https://cdn.example.com/master.m3u8")
    assert len(variants) == 2
    assert variants[0]["url"] == "https://cdn.example.com/low/index.m3u8"
    assert variants[1]["height"] == 1080


def test_select_m3u8_variant_prefers_requested_quality():
    variants = [
        {"url": "360.m3u8", "width": 640, "height": 360, "bandwidth": 800000, "average_bandwidth": 0, "label": "360p"},
        {"url": "720.m3u8", "width": 1280, "height": 720, "bandwidth": 1800000, "average_bandwidth": 0, "label": "720p"},
        {"url": "1080.m3u8", "width": 1920, "height": 1080, "bandwidth": 2800000, "average_bandwidth": 0, "label": "1080p"},
    ]
    assert select_m3u8_variant(variants, "best")["url"] == "1080.m3u8"
    assert select_m3u8_variant(variants, "720p")["url"] == "720.m3u8"
    assert select_m3u8_variant(variants, "worst")["url"] == "360.m3u8"


def test_resolve_stream_variant_chooses_best(monkeypatch):
    playlist = """
#EXTM3U
#EXT-X-STREAM-INF:BANDWIDTH=800000,RESOLUTION=640x360
low/index.m3u8
#EXT-X-STREAM-INF:BANDWIDTH=2800000,RESOLUTION=1920x1080
hi/index.m3u8
"""
    monkeypatch.setattr(core, "fetch_text_content", lambda *args, **kwargs: playlist)
    result = resolve_stream_variant(
        "https://cdn.example.com/master.m3u8",
        {},
        timeout=10,
        quality="best",
    )
    assert result["is_master"] is True
    assert result["quality"] == "1080p"
    assert result["url"] == "https://cdn.example.com/hi/index.m3u8"


def test_download_file_reports_progress(tmp_path, monkeypatch):
    logs = []
    payload = [b"a" * (1024 * 1024), b"b" * (1024 * 1024)]
    response = DummyResponse(
        payload,
        headers={"Content-Length": str(2 * 1024 * 1024)},
        url="https://cdn.example.com/video.mp4",
    )

    monkeypatch.setattr(core.requests, "get", lambda *args, **kwargs: response)

    output_path = tmp_path / "video.mp4"
    result = core.download_file(
        "https://example.com/video.mp4",
        {},
        str(output_path),
        timeout=10,
        overwrite=True,
        progress_callback=logs.append,
    )

    assert output_path.read_bytes() == b"".join(payload)
    assert result == f"{output_path} ({2 * 1024 * 1024} bytes)"
    assert logs[0] == f"[download] 開始下載：{output_path}"
    assert logs[1] == "[download] 最終網址：https://cdn.example.com/video.mp4"
    assert "[download] 檔案大小：2.0 MB" in logs
    assert any("進度  50%" in log for log in logs)
    assert any("進度 100%" in log for log in logs)
    assert logs[-1] == f"[download] 下載完成：{output_path} (2.0 MB)"


def test_download_file_reports_bytes_when_length_unknown(tmp_path, monkeypatch):
    logs = []
    payload = [b"a" * (4 * 1024 * 1024), b"b" * (2 * 1024 * 1024)]
    response = DummyResponse(
        payload,
        headers={"Content-Type": "application/octet-stream"},
    )

    monkeypatch.setattr(core.requests, "get", lambda *args, **kwargs: response)

    output_path = tmp_path / "download"
    result = core.download_file(
        "https://example.com/download",
        {},
        str(output_path),
        timeout=10,
        overwrite=True,
        progress_callback=logs.append,
    )

    final_path = tmp_path / "download.bin"
    assert final_path.read_bytes() == b"".join(payload)
    assert result == f"{final_path} ({6 * 1024 * 1024} bytes)"
    assert "[download] 檔案大小：未知" in logs
    assert any(log == "[download] 已下載 6.0 MB" for log in logs)
    assert logs[-1] == f"[download] 下載完成：{final_path} (6.0 MB)"
