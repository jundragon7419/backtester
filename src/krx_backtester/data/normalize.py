"""원시 응답 정규화와 당일 시가총액 계산 (P2-1)."""

from dataclasses import dataclass

import duckdb

# 무거래 행 판정은 시가 0 단독. 거래량 0인데 시가가 있는 행은 없고, 시가가 0인데
# 시간외 체결로 거래량만 있는 행은 있다 (M1 실측 125건, docs/M1_RESULTS.md).
NO_TRADE_CONDITION = "TDD_OPNPRC = '0'"
NUMERIC_COLUMNS = (
    "TDD_CLSPRC", "TDD_OPNPRC", "TDD_HGPRC", "TDD_LWPRC", "ACC_TRDVOL", "ACC_TRDVAL", "LIST_SHRS", "MKTCAP",
)
# 코스닥 소속부 표기는 2011-05-02부터 채워진다. 그 이전과 유가증권은 판별 불가(NULL)
MANAGED_FROM = "2011-05-02"
MANAGED_LABEL = "관리종목(소속부없음)"

SELECT_SQL = f"""
WITH base AS (
    SELECT date, code, market,
           CASE WHEN {NO_TRADE_CONDITION} THEN NULL ELSE CAST(TDD_OPNPRC AS BIGINT) END AS open,
           CASE WHEN {NO_TRADE_CONDITION} THEN NULL ELSE CAST(TDD_HGPRC AS BIGINT) END AS high,
           CASE WHEN {NO_TRADE_CONDITION} THEN NULL ELSE CAST(TDD_LWPRC AS BIGINT) END AS low,
           CAST(TDD_CLSPRC AS BIGINT) AS close,
           CAST(ACC_TRDVOL AS BIGINT) AS volume,
           CAST(ACC_TRDVAL AS HUGEINT) AS value,
           CAST(LIST_SHRS AS HUGEINT) AS listed_shares,
           CAST(TDD_CLSPRC AS HUGEINT) * CAST(LIST_SHRS AS HUGEINT) AS market_cap,
           {NO_TRADE_CONDITION} AS no_trade,
           CAST(ACC_TRDVOL AS BIGINT) > 0 AS has_trade,
           CASE WHEN market = 'KOSDAQ' AND date >= DATE '{MANAGED_FROM}'
                THEN SECT_TP_NM = '{MANAGED_LABEL}' END AS is_managed
    FROM raw_daily
),
islands AS (
    SELECT *, row_number() OVER (PARTITION BY code ORDER BY date)
              - row_number() OVER (PARTITION BY code, no_trade ORDER BY date) AS island
    FROM base
),
runs AS (
    SELECT *, CASE WHEN no_trade
                   THEN count(*) OVER (PARTITION BY code, no_trade, island ORDER BY date ROWS UNBOUNDED PRECEDING)
                   ELSE 0 END AS run_len
    FROM islands
)
SELECT date, code, market, open, high, low, close, volume, value, listed_shares, market_cap,
       no_trade, has_trade,
       coalesce(lag(run_len) OVER w, 0) AS halt_run,
       is_managed,
       sum(CASE WHEN has_trade THEN 1 ELSE 0 END) OVER (PARTITION BY code ORDER BY date ROWS BETWEEN 19 PRECEDING AND CURRENT ROW) AS valid_days_20
FROM runs
WINDOW w AS (PARTITION BY code ORDER BY date)
"""


class NonNumericValue(Exception):
    """원시 응답에 숫자가 아닌 값이 있어 정규화를 중단한다."""


@dataclass
class NormalizeResult:
    rows: int
    no_trade: int
    no_trade_with_close_move: int
    managed_known: int


def check_numeric(con: duckdb.DuckDBPyConnection) -> None:
    """캐스팅 실패 행이 있으면 예시와 함께 중단한다. 값을 조용히 NULL로 만들지 않는다."""
    condition = " OR ".join(f"TRY_CAST({col} AS HUGEINT) IS NULL" for col in NUMERIC_COLUMNS)
    bad = con.execute(f"SELECT count(*) FROM raw_daily WHERE {condition}").fetchone()[0]
    if bad:
        sample = con.execute(
            f"SELECT date, code, {', '.join(NUMERIC_COLUMNS)} FROM raw_daily WHERE {condition} LIMIT 3"
        ).fetchall()
        raise NonNumericValue(f"숫자가 아닌 값 {bad}행. 예시: {sample}")


def build_prices(con: duckdb.DuckDBPyConnection, memory_limit: str = "2GB", threads: int = 4) -> NormalizeResult:
    """raw_daily → prices 전량 재생성 (멱등)."""
    check_numeric(con)
    con.execute(f"PRAGMA memory_limit='{memory_limit}'")
    con.execute(f"PRAGMA threads={threads}")
    con.execute("DELETE FROM prices")
    con.execute(f"INSERT INTO prices {SELECT_SQL}")
    # prices가 바뀌면 그 위에서 만든 이벤트 후보는 무효다 (adjust로 다시 만든다)
    dropped = con.execute("SELECT count(*) FROM adj_factors").fetchone()[0]
    con.execute("DELETE FROM adj_factors")
    con.execute("CHECKPOINT")
    if dropped:
        print(f"경고: prices를 다시 만들어 adj_factors {dropped:,}행을 비웠습니다. adjust를 다시 실행하세요.")
    rows, no_trade, managed = con.execute(
        "SELECT count(*), count(*) FILTER (no_trade), count(is_managed) FROM prices"
    ).fetchone()
    # 무거래인데 종가가 움직인 행(기세·시간외 체결). 품질 리포트 추적용
    moved = con.execute(
        """
        SELECT count(*) FROM (
            SELECT close, no_trade, lag(close) OVER (PARTITION BY code ORDER BY date) AS prev_close FROM prices
        ) WHERE no_trade AND prev_close IS NOT NULL AND close <> prev_close
        """
    ).fetchone()[0]
    return NormalizeResult(rows, no_trade, moved, managed)
