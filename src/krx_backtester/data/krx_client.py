"""KRX Open API 단일 호출과 응답 원문 저장."""

import os
from dataclasses import dataclass
from pathlib import Path

import keyring
import requests

# 출처: openapi.krx.co.kr 서비스별 개발 명세서 (Server endpoint url), 확인일 2026-09-17
BASE_URL = "https://data-dbg.krx.co.kr/svc/apis"
AUTH_HEADER = "AUTH_KEY"
SERVICES = {
    "stk_bydd_trd": "sto/stk_bydd_trd",  # 유가증권 일별매매정보
    "ksq_bydd_trd": "sto/ksq_bydd_trd",  # 코스닥 일별매매정보
    "stk_isu_base_info": "sto/stk_isu_base_info",  # 유가증권 종목기본정보
    "ksq_isu_base_info": "sto/ksq_isu_base_info",  # 코스닥 종목기본정보
    "kospi_dd_trd": "idx/kospi_dd_trd",  # KOSPI 시리즈 일별시세정보
}

KEYRING_SERVICE = "krx-backtester"
KEYRING_USERNAME = "KRX_API_KEY"
ENV_VAR = "KRX_API_KEY"
DEFAULT_RAW_DIR = Path("data") / "raw"


@dataclass
class KrxResponse:
    status_code: int
    text: str
    saved_path: Path | None


def get_api_key() -> str:
    key = keyring.get_password(KEYRING_SERVICE, KEYRING_USERNAME) or os.environ.get(ENV_VAR)
    if not key:
        raise RuntimeError(
            f"API 키 없음: keyring({KEYRING_SERVICE}/{KEYRING_USERNAME}) 또는 환경변수 {ENV_VAR}를 설정하세요."
        )
    return key


def fetch(service: str, bas_dd: str, raw_dir: Path = DEFAULT_RAW_DIR, timeout: float = 30.0) -> KrxResponse:
    """단일 서비스·단일 기준일 호출. HTTP 200이면 원문을 raw_dir/{service}/{basDd}.json에 저장한다.

    HTTP 상태와 응답 본문(오류 메시지 포함)은 가공하지 않고 그대로 돌려준다.
    """
    resp = requests.get(
        f"{BASE_URL}/{SERVICES[service]}",
        params={"basDd": bas_dd},
        headers={AUTH_HEADER: get_api_key()},
        timeout=timeout,
    )
    saved_path = None
    if resp.status_code == 200:
        saved_path = raw_dir / service / f"{bas_dd}.json"
        saved_path.parent.mkdir(parents=True, exist_ok=True)
        saved_path.write_bytes(resp.content)
    return KrxResponse(resp.status_code, resp.content.decode("utf-8", errors="replace"), saved_path)
