"""기준가 역산 보정 계수, 이벤트 분류, adj_*·ret_* 가격 생성 (P2-2~5)."""

from dataclasses import dataclass

import duckdb

from krx_backtester.data.market_rules import tick_sql

# 계수 = 기준가 / 직전 행 종가. 분모는 무거래·기세 행도 포함한 직전 행이다.
# KRX의 `대비`가 그 기준으로 산출되며, 직전 비정지 종가로 나누면 기세 행에서만
# 가짜 이벤트가 6만 건 생긴다 (M1 실측).
CANDIDATE_SQL = """
WITH cal AS (
    SELECT date, row_number() OVER (ORDER BY date) AS idx FROM (SELECT DISTINCT date FROM prices)
),
seq AS (
    SELECT p.date, p.code, p.market, p.no_trade, p.has_trade, c.idx,
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
       base_price, prev_close, prev_date, has_trade,
       abs(base_price - prev_close) <= ({tick}) AS within_one_tick,
       prev_no_trade, no_trade AS row_no_trade, idx - prev_idx > 1 AS prev_gap
FROM seq
WHERE prev_close IS NOT NULL AND prev_close > 0 AND base_price <> prev_close
""".replace("{tick}", tick_sql(price="prev_close", date="date", market="market"))

# 무거래 구간의 계수는 곱해서 재개 후 첫 거래일에 귀속시킨다 (P2-3). 재개일이 없으면 unattributed.
EVENTS_SQL = """
WITH cand AS (SELECT * FROM candidates WHERE NOT within_one_tick AND NOT prev_gap),
attributed AS (
    SELECT c.*,
           CASE WHEN c.has_trade THEN c.date
                ELSE (SELECT min(p.date) FROM prices p
                      WHERE p.code = c.code AND p.date > c.date AND p.has_trade) END AS applies_on
    FROM cand c
),
grouped AS (
    SELECT code, applies_on, product(factor) AS factor, count(*) AS component_count,
           min(date) AS first_component_date, min(prev_date) AS first_prev_date,
           arg_max(base_price, date) AS base_price, arg_min(prev_close, date) AS prev_close,
           bool_or(prev_no_trade) AS any_prev_no_trade
    FROM attributed WHERE applies_on IS NOT NULL GROUP BY code, applies_on
),
joined AS (
    SELECT g.*, p.listed_shares AS shares_now, p.halt_run,
           (SELECT pp.listed_shares FROM prices pp WHERE pp.code = g.code AND pp.date = g.first_prev_date) AS shares_prev
    FROM grouped g JOIN prices p ON p.code = g.code AND p.date = g.applies_on
),
classified AS (
    SELECT *,
           CASE
               WHEN any_prev_no_trade AND shares_prev IS NOT NULL AND shares_prev > 0
                    AND shares_now <> shares_prev
                    AND abs((shares_now * 1.0 / shares_prev) - (1 / factor)) / (1 / factor) <= {share_tolerance}
                    THEN 'share_change'
               WHEN any_prev_no_trade THEN 'restructure'
               WHEN factor < 1 THEN 'rights'
               ELSE 'unverified'
           END AS event_type
    FROM joined
)
SELECT applies_on AS date, code, factor, base_price, prev_close, first_prev_date AS prev_date,
       component_count, first_component_date, shares_prev, shares_now, halt_run,
       event_type,
       CASE WHEN event_type = 'rights' THEN prev_close - base_price END AS rights_value_per_share,
       applies_on AS known_date, NULL AS post_verification
FROM classified
UNION ALL
-- 귀속할 거래일이 없는 이벤트(폐지 직전 정지 구간). 엔진은 적용하지 않고 품질 리포트에만 집계
SELECT c.date, c.code, c.factor, c.base_price, c.prev_close, c.prev_date,
       1, c.date, NULL, NULL, NULL, 'unattributed', NULL, c.date, NULL
FROM candidates c
WHERE NOT c.within_one_tick AND NOT c.prev_gap AND NOT c.has_trade
  AND NOT EXISTS (SELECT 1 FROM prices p WHERE p.code = c.code AND p.date > c.date AND p.has_trade)
"""

# 주식수 변화율과 1/계수의 허용 오차. 호가 단위 반올림 때문에 정확히 일치하지 않는다
# (5:2 분할에서 1/계수가 4.9889로 나오는 사례 등, M1 실측 124건).
SHARE_RATIO_TOLERANCE = 0.01
EVENT_TYPES = ("share_change", "restructure", "rights", "unverified", "unattributed")


class PricesOutOfDate(Exception):
    """prices가 raw_daily와 어긋나 계수를 산출할 수 없다."""


@dataclass
class AdjustResult:
    candidates: int
    events: int
    within_one_tick: int
    prev_gap: int
    multi_component: int
    by_type: dict[str, int]


def build_adj_factors(con: duckdb.DuckDBPyConnection, memory_limit: str = "2GB", threads: int = 4) -> AdjustResult:
    """이벤트 후보를 산출해 귀속·분류한 뒤 adj_factors를 전량 재생성한다 (멱등)."""
    prices_rows, raw_rows = con.execute(
        "SELECT (SELECT count(*) FROM prices), (SELECT count(*) FROM raw_daily)"
    ).fetchone()
    if prices_rows != raw_rows:
        raise PricesOutOfDate(f"prices {prices_rows:,}행 ≠ raw_daily {raw_rows:,}행. normalize를 먼저 실행하세요.")

    con.execute(f"PRAGMA memory_limit='{memory_limit}'")
    con.execute(f"PRAGMA threads={threads}")
    con.execute(f"CREATE OR REPLACE TEMP TABLE candidates AS {CANDIDATE_SQL}")
    candidates, within_tick, gap = con.execute(
        "SELECT count(*), count(*) FILTER (within_one_tick), count(*) FILTER (prev_gap) FROM candidates"
    ).fetchone()

    con.execute("DELETE FROM adj_factors")
    con.execute(f"INSERT INTO adj_factors {EVENTS_SQL.replace('{share_tolerance}', str(SHARE_RATIO_TOLERANCE))}")
    con.execute("DROP TABLE candidates")
    con.execute("CHECKPOINT")

    events, multi = con.execute(
        "SELECT count(*), count(*) FILTER (component_count > 1) FROM adj_factors"
    ).fetchone()
    by_type = dict(con.execute("SELECT event_type, count(*) FROM adj_factors GROUP BY 1").fetchall())
    return AdjustResult(candidates, events, within_tick, gap, multi, by_type)
