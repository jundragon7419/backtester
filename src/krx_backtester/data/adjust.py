"""기준가 역산 보정 계수, 이벤트 분류, adj_*·ret_* 가격 생성 (P2-2~5)."""

from dataclasses import dataclass

import duckdb

from krx_backtester.data.market_rules import tick_sql

# 계수 = 기준가 / 직전 행 종가. 분모는 무거래·기세 행도 포함한 직전 행이다.
# KRX의 `대비`가 그 기준으로 산출되며, 직전 비정지 종가로 나누면 기세 행에서만
# 가짜 이벤트가 6만 건 생긴다 (docs/M1_RESULTS.md 기반 실측).
CANDIDATE_SQL = """
WITH cal AS (
    SELECT date, row_number() OVER (ORDER BY date) AS idx FROM (SELECT DISTINCT date FROM prices)
),
seq AS (
    SELECT p.date, p.code, p.market, p.no_trade, c.idx,
           p.close - CAST(r.CMPPREVDD_PRC AS BIGINT) AS base_price,
           lag(p.close) OVER w AS prev_close,
           lag(p.date) OVER w AS prev_date,
           lag(p.no_trade) OVER w AS prev_no_trade,
           lag(c.idx) OVER w AS prev_idx
    FROM prices p
    JOIN raw_daily r ON r.date = p.date AND r.code = p.code
    JOIN cal c ON c.date = p.date
    WINDOW w AS (PARTITION BY p.code ORDER BY c.idx)
)
SELECT date, code, base_price * 1.0 / prev_close AS factor,
       base_price, prev_close, prev_date,
       abs(base_price - prev_close) <= ({tick}) AS within_one_tick,
       prev_no_trade, no_trade AS row_no_trade, idx - prev_idx > 1 AS prev_gap,
       NULL AS event_type, NULL AS rights_value_per_share, date AS known_date, NULL AS post_verification
FROM seq
WHERE prev_close IS NOT NULL AND prev_close > 0 AND base_price <> prev_close
""".replace("{tick}", tick_sql(price="prev_close", date="date", market="market"))


class PricesOutOfDate(Exception):
    """prices가 raw_daily와 어긋나 계수를 산출할 수 없다."""


@dataclass
class AdjustResult:
    candidates: int
    events: int
    within_one_tick: int
    prev_gap: int
    row_no_trade: int
    prev_no_trade: int


def build_adj_factors(con: duckdb.DuckDBPyConnection, memory_limit: str = "2GB", threads: int = 4) -> AdjustResult:
    """이벤트 후보를 전량 재생성한다 (멱등). 이벤트 판정은 정수 비교로 한다."""
    prices_rows, raw_rows = con.execute(
        "SELECT (SELECT count(*) FROM prices), (SELECT count(*) FROM raw_daily)"
    ).fetchone()
    if prices_rows != raw_rows:
        raise PricesOutOfDate(f"prices {prices_rows:,}행 ≠ raw_daily {raw_rows:,}행. normalize를 먼저 실행하세요.")

    con.execute(f"PRAGMA memory_limit='{memory_limit}'")
    con.execute(f"PRAGMA threads={threads}")
    con.execute("DELETE FROM adj_factors")
    con.execute(f"INSERT INTO adj_factors {CANDIDATE_SQL}")
    con.execute("CHECKPOINT")

    candidates, within_tick, gap, row_no_trade, prev_no_trade = con.execute(
        """
        SELECT count(*), count(*) FILTER (within_one_tick), count(*) FILTER (prev_gap),
               count(*) FILTER (row_no_trade), count(*) FILTER (prev_no_trade)
        FROM adj_factors
        """
    ).fetchone()
    events = con.execute(
        "SELECT count(*) FROM adj_factors WHERE NOT within_one_tick AND NOT prev_gap"
    ).fetchone()[0]
    return AdjustResult(candidates, events, within_tick, gap, row_no_trade, prev_no_trade)
