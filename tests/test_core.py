from m3u8_helper.core import extract_title, find_m3u8_candidates, guess_output_name, make_title_based_name


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
