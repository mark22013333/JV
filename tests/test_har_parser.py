from m3u8_helper.har_parser import extract_media_candidates


def test_extract_media_candidates_m3u8():
    har = {
        "log": {
            "entries": [
                {"request": {"url": "https://example.com/video/master.m3u8"}, "response": {"content": {}}},
                {"request": {"url": "https://example.com/track"}, "response": {"content": {"mimeType": "video/mp4"}}},
            ]
        }
    }
    items = extract_media_candidates(har)
    urls = [u for (u, _m) in items]
    assert "https://example.com/video/master.m3u8" in urls
    assert "https://example.com/track" in urls
