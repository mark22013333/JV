from m3u8_helper.curl_parser import parse_curl_command


def test_parse_curl_basic():
    cmd = """
    curl 'https://example.com/playlist.m3u8' \
      -H 'User-Agent: UA' \
      -H 'Accept: */*' \
      -H 'Referer: https://example.com/watch'
    """
    url, headers = parse_curl_command(cmd)
    assert url == "https://example.com/playlist.m3u8"
    assert headers["User-Agent"] == "UA"
    assert headers["Accept"] == "*/*"
    assert headers["Referer"] == "https://example.com/watch"


def test_parse_curl_user_agent_flag():
    cmd = "curl https://example.com/playlist.m3u8 -A 'UA-1'"
    url, headers = parse_curl_command(cmd)
    assert url == "https://example.com/playlist.m3u8"
    assert headers["User-Agent"] == "UA-1"
