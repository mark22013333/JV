from pathlib import Path

import pytest

from m3u8_helper.settings import SettingsError, SettingsManager


def test_settings_updates_absolute_writable_output_directory(tmp_path):
    settings = SettingsManager(tmp_path / "config" / "settings.json")
    output_dir = tmp_path / "downloads"

    result = settings.update_output_dir(str(output_dir))

    assert result["output_dir"] == str(output_dir.resolve())
    assert output_dir.is_dir()
    assert not (output_dir / ".m3u8-helper-write-test").exists()


def test_settings_rejects_relative_output_directory(tmp_path):
    settings = SettingsManager(tmp_path / "settings.json")
    with pytest.raises(SettingsError, match="絕對路徑"):
        settings.update_output_dir("relative/downloads")


def test_pairing_issues_and_validates_bearer_token(tmp_path):
    settings = SettingsManager(tmp_path / "settings.json")
    pairing = settings.new_pairing_code()
    origin = "chrome-extension://abcdefghijklmnop"

    token = settings.pair(str(pairing["code"]), origin)

    assert settings.authorize_extension(origin, f"Bearer {token}") is True
    assert settings.authorize_extension(origin, "Bearer wrong") is False
    assert settings.public_settings()["paired"] is True


def test_pairing_rejects_non_extension_origin(tmp_path):
    settings = SettingsManager(tmp_path / "settings.json")
    code = settings.new_pairing_code()["code"]
    with pytest.raises(SettingsError, match="擴充功能"):
        settings.pair(str(code), "https://example.com")


def test_settings_file_does_not_expose_token_in_public_settings(tmp_path):
    settings = SettingsManager(tmp_path / "settings.json")
    code = settings.new_pairing_code()["code"]
    settings.pair(str(code), "chrome-extension://abc")
    public = settings.public_settings()

    assert "extension_token" not in public
    assert "token" not in public
