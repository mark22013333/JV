"""Local settings and browser-extension pairing for M3U8 Helper."""

from __future__ import annotations

import json
import secrets
import threading
import time
from pathlib import Path
from typing import Any, Dict


PAIRING_TTL_SECONDS = 10 * 60


class SettingsError(ValueError):
    """Raised when a local setting is invalid."""


class SettingsManager:
    def __init__(self, config_path: Path | None = None) -> None:
        self.config_path = config_path or Path.home() / ".m3u8-helper" / "settings.json"
        self._lock = threading.RLock()
        self._data: Dict[str, Any] = {
            "output_dir": str(Path.home() / "Downloads" / "M3U8 Helper"),
            "extension_origin": "",
            "extension_token": "",
        }
        self._pairing_code = ""
        self._pairing_expires_at = 0.0
        self._load()

    def _load(self) -> None:
        try:
            loaded = json.loads(self.config_path.read_text(encoding="utf-8"))
        except (FileNotFoundError, OSError, json.JSONDecodeError):
            return
        if isinstance(loaded, dict):
            for key in self._data:
                value = loaded.get(key)
                if isinstance(value, str):
                    self._data[key] = value

    def _save(self) -> None:
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = self.config_path.with_suffix(".tmp")
        temp_path.write_text(
            json.dumps(self._data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        try:
            temp_path.chmod(0o600)
        except OSError:
            pass
        temp_path.replace(self.config_path)

    @staticmethod
    def is_extension_origin(origin: str) -> bool:
        return origin.startswith("chrome-extension://") or origin.startswith("edge-extension://")

    def public_settings(self) -> Dict[str, object]:
        with self._lock:
            return {
                "output_dir": self._data["output_dir"],
                "paired": bool(self._data["extension_token"]),
                "extension_origin": self._data["extension_origin"],
            }

    @property
    def output_dir(self) -> Path:
        with self._lock:
            return Path(self._data["output_dir"])

    def ensure_output_dir(self) -> Path:
        output_dir = self.output_dir
        try:
            output_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise SettingsError(f"無法建立下載資料夾：{exc}") from exc
        if not output_dir.is_dir():
            raise SettingsError("下載位置不是資料夾。")
        return output_dir

    def update_output_dir(self, raw_path: str) -> Dict[str, object]:
        if not raw_path.strip():
            raise SettingsError("下載資料夾不可為空。")
        candidate = Path(raw_path).expanduser()
        if not candidate.is_absolute():
            raise SettingsError("下載資料夾必須是絕對路徑。")
        try:
            candidate.mkdir(parents=True, exist_ok=True)
            probe = candidate / ".m3u8-helper-write-test"
            probe.touch(exist_ok=True)
            probe.unlink()
        except OSError as exc:
            raise SettingsError(f"下載資料夾無法寫入：{exc}") from exc
        with self._lock:
            self._data["output_dir"] = str(candidate.resolve())
            self._save()
        return self.public_settings()

    def new_pairing_code(self) -> Dict[str, object]:
        with self._lock:
            self._pairing_code = f"{secrets.randbelow(1_000_000):06d}"
            self._pairing_expires_at = time.time() + PAIRING_TTL_SECONDS
            return {
                "code": self._pairing_code,
                "expires_at": int(self._pairing_expires_at),
                "expires_in": PAIRING_TTL_SECONDS,
            }

    def pair(self, code: str, origin: str) -> str:
        if not self.is_extension_origin(origin):
            raise SettingsError("只接受 Chrome 或 Edge 擴充功能配對。")
        with self._lock:
            if not self._pairing_code or time.time() > self._pairing_expires_at:
                raise SettingsError("配對碼已過期，請在本機工具重新產生。")
            if not secrets.compare_digest(code.strip(), self._pairing_code):
                raise SettingsError("配對碼不正確。")
            token = secrets.token_urlsafe(32)
            self._data["extension_origin"] = origin
            self._data["extension_token"] = token
            self._pairing_code = ""
            self._pairing_expires_at = 0.0
            self._save()
            return token

    def authorize_extension(self, origin: str, authorization: str) -> bool:
        with self._lock:
            expected_origin = self._data["extension_origin"]
            expected_token = self._data["extension_token"]
        if not expected_origin or not expected_token or origin != expected_origin:
            return False
        prefix = "Bearer "
        if not authorization.startswith(prefix):
            return False
        return secrets.compare_digest(authorization[len(prefix) :], expected_token)


__all__ = ["PAIRING_TTL_SECONDS", "SettingsError", "SettingsManager"]
