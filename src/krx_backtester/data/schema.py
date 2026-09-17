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
)


def init_schema(con: duckdb.DuckDBPyConnection) -> None:
    for stmt in DDL:
        con.execute(stmt)


def connect(db_path: Path) -> duckdb.DuckDBPyConnection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(db_path))
    init_schema(con)
    return con
