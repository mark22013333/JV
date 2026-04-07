from m3u8_helper import core
from m3u8_helper.core import extract_title, find_m3u8_candidates, guess_output_name, make_title_based_name


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
