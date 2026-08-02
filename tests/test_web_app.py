import json
import threading
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from m3u8_helper.jobs import DownloadJobManager
from m3u8_helper.settings import SettingsManager
from m3u8_helper.web_app import build_server


def request_json(base_url, path, method="GET", payload=None, headers=None):
    body = json.dumps(payload or {}).encode("utf-8") if method != "GET" else None
    request = Request(
        base_url + path,
        data=body,
        method=method,
        headers={"Content-Type": "application/json", **(headers or {})},
    )
    try:
        with urlopen(request, timeout=2) as response:
            return response.status, json.loads(response.read().decode("utf-8")), response.headers
    except HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8")), exc.headers


def test_health_pairing_and_authorized_settings_api(tmp_path):
    settings = SettingsManager(tmp_path / "settings.json")
    jobs = DownloadJobManager(settings, tmp_path / "jobs.json", start_worker=False)
    server = build_server("127.0.0.1", 0, settings, jobs)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base_url = f"http://127.0.0.1:{server.server_address[1]}"
    origin = "chrome-extension://abcdefghijklmnop"
    try:
        status, health, _ = request_json(base_url, "/api/health")
        assert status == 200
        assert health["status"] == "ok"

        _, pairing, _ = request_json(base_url, "/api/pairing-code", "POST")
        status, paired, cors_headers = request_json(
            base_url,
            "/api/pair",
            "POST",
            {"code": pairing["code"]},
            {"Origin": origin},
        )
        assert status == 200
        assert cors_headers["Access-Control-Allow-Origin"] == origin

        auth_headers = {"Origin": origin, "Authorization": f"Bearer {paired['token']}"}
        status, result, _ = request_json(base_url, "/api/settings", headers=auth_headers)
        assert status == 200
        assert result["paired"] is True

        status, result, _ = request_json(
            base_url,
            "/api/settings",
            headers={"Origin": origin, "Authorization": "Bearer wrong"},
        )
        assert status == 401
        assert "權杖" in result["error"]

        malformed = Request(
            base_url + "/api/jobs",
            data=b"{",
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        try:
            urlopen(malformed, timeout=2)
            assert False, "無效 JSON 應回傳 HTTP 400"
        except HTTPError as exc:
            assert exc.code == 400
            body = json.loads(exc.read().decode("utf-8"))
            assert "JSON" in body["error"]
    finally:
        server.shutdown()
        server.server_close()
