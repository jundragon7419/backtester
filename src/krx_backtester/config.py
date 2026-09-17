"""config/default.toml 로드와 설정 객체 제공."""

import tomllib
from datetime import date
from pathlib import Path

DEFAULT_CONFIG_PATH = Path("config") / "default.toml"
KEY_EXPIRY_WARN_DAYS = 30


def load_config(path: Path = DEFAULT_CONFIG_PATH) -> dict:
    with path.open("rb") as f:
        return tomllib.load(f)


def key_expiry_warning(config: dict, today: date) -> str | None:
    """만료일 미기입이거나 30일 이내면 경고 문구를 돌려준다."""
    raw = config.get("api", {}).get("api_key_expires_on", "")
    if not raw:
        return "경고: config/default.toml의 api_key_expires_on이 비어 있어 인증키 만료를 확인할 수 없습니다."
    days_left = (date.fromisoformat(raw) - today).days
    if days_left <= KEY_EXPIRY_WARN_DAYS:
        return f"경고: 인증키 만료까지 {days_left}일 남았습니다 (만료일 {raw}). 마이페이지에서 연장하세요."
    return None
