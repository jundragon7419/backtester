"""보정 계수 타당성 검사 (P2-2)."""

from datetime import date, timedelta

import pytest
from conftest import daily_row
from test_halted_rows import insert

from krx_backtester.data.adjust import PricesOutOfDate, build_adj_factors
from krx_backtester.data.normalize import build_prices


def row(code: str, close: int, diff: int, *, halted: bool = False, shares: str = "100") -> dict:
    """종가와 대비를 지정한 가짜 행. 기준가 = 종가 − 대비."""
    values = daily_row(code, open_price="0" if halted else str(close), volume="0" if halted else "100")
    values.update(
        TDD_CLSPRC=str(close), CMPPREVDD_PRC=str(diff),
        TDD_HGPRC="0" if halted else str(close), TDD_LWPRC="0" if halted else str(close),
        ACC_TRDVAL="0" if halted else "1000", LIST_SHRS=shares, MKTCAP=str(close * int(shares)),
    )
    return values


def factors(con) -> dict:
    cols = "date, factor, base_price, prev_close, prev_date, within_one_tick, prev_halted, row_halted, prev_gap, known_date"
    rows = con.execute(f"SELECT {cols} FROM adj_factors ORDER BY date").fetchall()
    keys = [c.strip() for c in cols.split(",")]
    return {r[0]: dict(zip(keys, r)) for r in rows}


def load(con, series: list[tuple[date, dict]]) -> None:
    for day, values in series:
        insert(con, day, "KOSPI", values)
    build_prices(con)


def test_normal_days_have_no_event(con):
    d = date(2020, 1, 2)
    load(con, [(d, row("000001", 1000, 0)), (d + timedelta(days=1), row("000001", 1100, 100))])
    build_adj_factors(con)

    assert factors(con) == {}


def test_day_after_no_trade_row_uses_previous_row_close(con):
    """기세로 종가가 움직인 다음 날도 계수 1. 청사진의 '직전 비정지 종가' 정의였다면 이벤트로 잡혔을 케이스."""
    d = date(2020, 1, 2)
    load(con, [
        (d, row("000002", 1000, 0)),
        (d + timedelta(days=1), row("000002", 1050, 50, halted=True)),  # 무거래인데 종가 이동
        (d + timedelta(days=2), row("000002", 1080, 30)),               # 기준가 1050 = 직전 행 종가
    ])
    build_adj_factors(con)

    assert factors(con) == {}


def test_split_after_halt_gives_exact_factor(con):
    d = date(2020, 1, 2)
    load(con, [
        (d, row("000003", 5000, 0)),
        (d + timedelta(days=1), row("000003", 5000, 0, halted=True)),
        (d + timedelta(days=2), row("000003", 1010, 10)),  # 기준가 1000 = 5000 / 5
    ])
    build_adj_factors(con)

    event = factors(con)[d + timedelta(days=2)]
    assert event["factor"] == pytest.approx(0.2)
    assert event["base_price"] == 1000 and event["prev_close"] == 5000
    assert event["prev_halted"] is True and event["row_halted"] is False
    assert event["known_date"] == event["date"]


def test_one_tick_difference_is_flagged_not_counted(con):
    d = date(2020, 1, 2)
    # 15,000원대는 임시 호가표에서 개편 전 50원
    load(con, [(d, row("000004", 15000, 0)), (d + timedelta(days=1), row("000004", 15100, 50))])
    result = build_adj_factors(con)

    event = factors(con)[d + timedelta(days=1)]
    assert event["within_one_tick"] is True
    assert result.candidates == 1 and result.events == 0


def test_gap_in_series_is_excluded_from_events(con):
    other = "000009"
    days = [date(2020, 1, 2) + timedelta(days=i) for i in range(4)]
    for day in days:  # 거래일 달력을 만들기 위한 다른 종목
        insert(con, day, "KOSPI", row(other, 500, 0))
    insert(con, days[0], "KOSPI", row("000005", 1000, 0))
    insert(con, days[3], "KOSPI", row("000005", 300, 0))  # 재상장: 기준가 300 ≠ 직전 종가 1000
    build_prices(con)
    result = build_adj_factors(con)

    event = factors(con)[days[3]]
    assert event["prev_gap"] is True and event["prev_date"] == days[0]
    assert result.events == 0


def test_first_row_has_no_factor(con):
    d = date(2020, 1, 2)
    load(con, [(d, row("000006", 1000, 100))])
    build_adj_factors(con)

    assert factors(con) == {}


def test_rebuild_is_idempotent(con):
    d = date(2020, 1, 2)
    load(con, [(d, row("000007", 5000, 0)), (d + timedelta(days=1), row("000007", 1010, 10))])
    first = build_adj_factors(con)
    second = build_adj_factors(con)

    assert first == second
    assert con.execute("SELECT count(*) FROM adj_factors").fetchone()[0] == 1


def test_stale_prices_stops_with_message(con):
    d = date(2020, 1, 2)
    load(con, [(d, row("000008", 1000, 0))])
    insert(con, d + timedelta(days=1), "KOSPI", row("000008", 1100, 100))  # prices 재생성 없이 원시만 추가

    with pytest.raises(PricesOutOfDate):
        build_adj_factors(con)


def test_normalize_clears_stale_adj_factors(con):
    d = date(2020, 1, 2)
    load(con, [(d, row("000010", 5000, 0)), (d + timedelta(days=1), row("000010", 1010, 10))])
    build_adj_factors(con)
    assert con.execute("SELECT count(*) FROM adj_factors").fetchone()[0] == 1

    build_prices(con)
    assert con.execute("SELECT count(*) FROM adj_factors").fetchone()[0] == 0
