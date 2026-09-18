"""정지·무거래 행 처리와 정규화 (P2-1, 불변 규칙 23)."""

from datetime import date, timedelta

import pytest
from conftest import daily_row

from krx_backtester.data.krx_client import DAILY_FIELDS
from krx_backtester.data.normalize import NonNumericValue, build_prices, check_numeric


def insert(con, d: date, market: str, row: dict, sect_tp_nm: str = "") -> None:
    row = {**row, "SECT_TP_NM": sect_tp_nm}
    con.execute(
        f"INSERT INTO raw_daily VALUES (?, ?, ?, {', '.join('?' for _ in DAILY_FIELDS)})",
        [d, market, row["ISU_CD"], *(row[f] for f in DAILY_FIELDS)],
    )


def traded(code: str, close: str = "1000", open_price: str = "1010", volume: str = "500") -> dict:
    row = daily_row(code, open_price=open_price, volume=volume)
    row.update(TDD_CLSPRC=close, TDD_HGPRC="1020", TDD_LWPRC="990", ACC_TRDVAL="500000", LIST_SHRS="100", MKTCAP="100000")
    return row


def halted(code: str, close: str = "1000", volume: str = "0") -> dict:
    row = daily_row(code, open_price="0", volume=volume)
    row.update(TDD_CLSPRC=close, TDD_HGPRC="0", TDD_LWPRC="0", ACC_TRDVAL="0", LIST_SHRS="100", MKTCAP="100000")
    return row


def fetch(con, code: str, d: date) -> dict:
    cols = "open, high, low, close, volume, is_halted, has_trade, is_managed, valid_days_20, market_cap"
    row = con.execute(f"SELECT {cols} FROM prices WHERE code = ? AND date = ?", [code, d]).fetchone()
    return dict(zip(cols.replace(" ", "").split(","), row))


def test_halted_row_has_null_prices_and_keeps_close(con):
    insert(con, date(2020, 1, 2), "KOSPI", traded("000001"))
    insert(con, date(2020, 1, 3), "KOSPI", halted("000001"))
    build_prices(con)

    row = fetch(con, "000001", date(2020, 1, 3))
    assert row["is_halted"] is True and row["has_trade"] is False
    assert row["open"] is None and row["high"] is None and row["low"] is None
    assert row["close"] == 1000
    assert row["market_cap"] == 100000


def test_zero_open_with_volume_is_halted(con):
    """시가·고가·저가 0인데 거래량만 있는 행(시간외 체결). 실측 125건 형태."""
    insert(con, date(2020, 1, 2), "KOSPI", halted("000002", volume="2"))
    build_prices(con)

    row = fetch(con, "000002", date(2020, 1, 2))
    assert row["is_halted"] is True and row["has_trade"] is True
    assert row["open"] is None


def test_open_present_is_not_halted_even_without_volume(con):
    insert(con, date(2020, 1, 2), "KOSPI", traded("000003", volume="0"))
    build_prices(con)

    row = fetch(con, "000003", date(2020, 1, 2))
    assert row["is_halted"] is False and row["has_trade"] is False
    assert row["open"] == 1010


@pytest.mark.parametrize(
    "market, day, sect, expected",
    [
        ("KOSDAQ", date(2011, 4, 29), "", None),
        ("KOSDAQ", date(2011, 5, 2), "관리종목(소속부없음)", True),
        ("KOSDAQ", date(2011, 5, 2), "우량기업부", False),
        ("KOSPI", date(2020, 1, 2), "", None),
    ],
)
def test_is_managed_source_and_start_date(con, market, day, sect, expected):
    insert(con, day, market, traded("000004"), sect_tp_nm=sect)
    build_prices(con)

    assert fetch(con, "000004", day)["is_managed"] is expected


def test_valid_days_20_counts_only_non_halted_in_last_20_rows(con):
    start = date(2020, 1, 2)
    for i in range(25):
        day = start + timedelta(days=i)
        row = halted("000005") if i in (5, 6, 7) else traded("000005")
        insert(con, day, "KOSPI", row)
    build_prices(con)

    # 마지막 행 기준 창은 5~24번째 행이고 정지 3개(5·6·7)가 그 안에 들어감
    assert fetch(con, "000005", start + timedelta(days=24))["valid_days_20"] == 17
    # 8번째 행 기준으로는 9개 행 중 정지 3개
    assert fetch(con, "000005", start + timedelta(days=8))["valid_days_20"] == 6


def test_rebuild_is_idempotent(con):
    insert(con, date(2020, 1, 2), "KOSPI", traded("000006"))
    first = build_prices(con)
    second = build_prices(con)

    assert first == second
    assert con.execute("SELECT count(*) FROM prices").fetchone()[0] == 1


def test_non_numeric_value_stops_with_example(con):
    row = traded("000007")
    row["TDD_CLSPRC"] = "-"
    insert(con, date(2020, 1, 2), "KOSPI", row)

    with pytest.raises(NonNumericValue) as err:
        check_numeric(con)
    assert "000007" in str(err.value)
