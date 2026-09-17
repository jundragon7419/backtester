"""수집기 테스트 공용 가짜 KRX 응답. 값은 모두 지어낸 것 (불변 규칙 25)."""

import json
from datetime import datetime

import duckdb
import pytest

from krx_backtester.data.collector import KST, Collector
from krx_backtester.data.krx_client import BASE_INFO_FIELDS, DAILY_FIELDS, INDEX_FIELDS, KrxResponse
from krx_backtester.data.schema import init_schema


def daily_row(code: str, open_price: str = "1000", volume: str = "10") -> dict:
    row = {f: "1" for f in DAILY_FIELDS}
    row.update(ISU_CD=code, ISU_NM=f"가짜{code}", TDD_OPNPRC=open_price, ACC_TRDVOL=volume)
    return row


def index_row(name: str = "가짜지수") -> dict:
    row = {f: "1" for f in INDEX_FIELDS}
    row.update(IDX_CLSS="KOSPI", IDX_NM=name)
    return row


def base_row(code: str) -> dict:
    row = {f: "1" for f in BASE_INFO_FIELDS}
    row.update(ISU_SRT_CD=code, ISU_CD=f"KR7{code}000")
    return row


class FakeKrx:
    """(service, basDd) → 행 목록 / KrxResponse / 예외. 등록 안 된 키는 빈 응답."""

    def __init__(self, responses: dict | None = None):
        self.responses = responses or {}
        self.calls: list[tuple[str, str]] = []

    def __call__(self, service: str, bas_dd: str) -> KrxResponse:
        self.calls.append((service, bas_dd))
        value = self.responses.get((service, bas_dd), [])
        if isinstance(value, Exception):
            raise value
        if isinstance(value, KrxResponse):
            return value
        return KrxResponse(200, json.dumps({"OutBlock_1": value}, ensure_ascii=False), None)


def trading_day(fake: FakeKrx, bas_dd: str, kospi: list[str], kosdaq: list[str]) -> None:
    fake.responses[("stk_bydd_trd", bas_dd)] = [daily_row(c) for c in kospi]
    fake.responses[("ksq_bydd_trd", bas_dd)] = [daily_row(c) for c in kosdaq]
    fake.responses[("kospi_dd_trd", bas_dd)] = [index_row()]
    fake.responses[("stk_isu_base_info", bas_dd)] = [base_row(c) for c in kospi]
    fake.responses[("ksq_isu_base_info", bas_dd)] = [base_row(c) for c in kosdaq]


@pytest.fixture
def con():
    connection = duckdb.connect()
    init_schema(connection)
    yield connection
    connection.close()


@pytest.fixture
def make_collector(con):
    def _make(fake: FakeKrx, now: datetime = datetime(2026, 1, 30, 12, tzinfo=KST), max_calls: int = 1000,
              max_consecutive_errors: int = 100) -> Collector:
        return Collector(con, fake, max_calls=max_calls, request_delay_seconds=0,
                         max_consecutive_errors=max_consecutive_errors, now=lambda: now, sleep=lambda s: None)
    return _make
