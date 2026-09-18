"""DuckDB 원시 계층 스키마와 instruments 테이블 생성."""

from pathlib import Path

import duckdb

from krx_backtester.data.krx_client import BASE_INFO_FIELDS, DAILY_FIELDS, INDEX_FIELDS


def _varchar_cols(fields: tuple[str, ...]) -> str:
    return ", ".join(f"{name} VARCHAR" for name in fields)


# 원시 테이블은 응답 필드를 문자열 그대로 저장한다. 숫자 변환은 P2 범위.
DDL = (
    f"CREATE TABLE IF NOT EXISTS raw_daily (date DATE, market VARCHAR, code VARCHAR, {_varchar_cols(DAILY_FIELDS)})",
    # 지수 코드가 없어 응답 필드 IDX_CLSS, IDX_NM이 키 컬럼 역할
    f"CREATE TABLE IF NOT EXISTS raw_index (date DATE, {_varchar_cols(INDEX_FIELDS)})",
    f"CREATE TABLE IF NOT EXISTS raw_isu_base (date DATE, market VARCHAR, code VARCHAR, {_varchar_cols(BASE_INFO_FIELDS)})",
    """CREATE TABLE IF NOT EXISTS collection_log (
        service VARCHAR, bas_dd DATE, status VARCHAR, http_status INTEGER,
        called_at TIMESTAMP, row_count INTEGER, message VARCHAR)""",
    """CREATE TABLE IF NOT EXISTS instruments (
        code VARCHAR, isu_std_cd VARCHAR, name VARCHAR, market VARCHAR,
        first_seen_date DATE, last_seen_date DATE, delisted_date DATE,
        is_right_censored BOOLEAN, is_preferred BOOLEAN)""",
    # 이벤트 후보. base_price·prev_close·prev_date는 판정 검산용, 플래그는 M2-c 분류 입력 (P2-2)
    """CREATE TABLE IF NOT EXISTS adj_factors (
        date DATE, code VARCHAR, factor DOUBLE,
        base_price BIGINT, prev_close BIGINT, prev_date DATE,
        component_count INTEGER, first_component_date DATE,
        shares_prev HUGEINT, shares_now HUGEINT, halt_run INTEGER,
        event_type VARCHAR, rights_value_per_share BIGINT, known_date DATE, post_verification VARCHAR)""",
    # 분석 계층. 무거래 행(시가 0)은 시·고·저가를 NULL로 둔다 (P2-1, 불변 규칙 23)
    """CREATE TABLE IF NOT EXISTS prices (
        date DATE, code VARCHAR, market VARCHAR,
        open BIGINT, high BIGINT, low BIGINT, close BIGINT,
        volume BIGINT, value HUGEINT, listed_shares HUGEINT, market_cap HUGEINT,
        no_trade BOOLEAN, has_trade BOOLEAN, halt_run INTEGER, is_managed BOOLEAN, valid_days_20 INTEGER)""",
)


# 원시 계층에서 언제든 다시 만들 수 있는 파생 테이블. 컬럼 구성이 바뀌면 비우고 다시 만든다.
DERIVED_COLUMNS = {
    "adj_factors": (
        "date", "code", "factor", "base_price", "prev_close", "prev_date",
        "component_count", "first_component_date", "shares_prev", "shares_now", "halt_run",
        "event_type", "rights_value_per_share", "known_date", "post_verification",
    ),
    "prices": (
        "date", "code", "market", "open", "high", "low", "close", "volume", "value", "listed_shares",
        "market_cap", "no_trade", "has_trade", "halt_run", "is_managed", "valid_days_20",
    ),
}


def _drop_outdated_derived_tables(con: duckdb.DuckDBPyConnection) -> list[str]:
    dropped = []
    for table, expected in DERIVED_COLUMNS.items():
        current = tuple(
            name for (name,) in con.execute(
                "SELECT column_name FROM duckdb_columns() WHERE table_name = ? ORDER BY column_index", [table]
            ).fetchall()
        )
        if current and current != expected:
            con.execute(f"DROP TABLE {table}")
            dropped.append(table)
    return dropped


def init_schema(con: duckdb.DuckDBPyConnection) -> list[str]:
    """스키마를 만들고, 컬럼이 바뀐 파생 테이블 이름 목록을 돌려준다."""
    dropped = _drop_outdated_derived_tables(con)
    for stmt in DDL:
        con.execute(stmt)
    return dropped


def connect(db_path: Path) -> duckdb.DuckDBPyConnection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(db_path))
    dropped = init_schema(con)
    if dropped:
        print(f"안내: 컬럼이 바뀌어 파생 테이블을 비웠습니다 ({', '.join(dropped)}). normalize·adjust를 다시 실행하세요.")
    return con
