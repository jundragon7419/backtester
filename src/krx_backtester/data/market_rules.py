"""날짜 구간별 가격제한폭·세율·수수료·호가 단위 제도 테이블 (P2-8)."""

# 임시 호가 단위 표. M0에서 적재 데이터를 역산해 만든 것으로, 공식 조문으로 확정되지 않았다
# (docs/M0_RESULTS.md "호가 단위"). 2010-10 개정 이전 구간은 표본이 없어 반영하지 못했다.
# M2-g에서 「유가증권·코스닥시장 업무규정 시행세칙」 조문 표로 교체하고 재산출해 대조한다.
PROVISIONAL_TICK_REFORM_DATE = "2023-01-25"

PROVISIONAL_TICK_SQL = """
CASE
    WHEN {date} >= DATE '2023-01-25' THEN
        CASE WHEN {price} < 2000 THEN 1 WHEN {price} < 5000 THEN 5 WHEN {price} < 20000 THEN 10
             WHEN {price} < 50000 THEN 50 WHEN {price} < 200000 THEN 100
             WHEN {price} < 500000 THEN 500 ELSE 1000 END
    WHEN {market} = 'KOSPI' THEN
        CASE WHEN {price} < 1000 THEN 1 WHEN {price} < 5000 THEN 5 WHEN {price} < 10000 THEN 10
             WHEN {price} < 50000 THEN 50 WHEN {price} < 100000 THEN 100
             WHEN {price} < 500000 THEN 500 ELSE 1000 END
    ELSE
        CASE WHEN {price} < 1000 THEN 1 WHEN {price} < 5000 THEN 5 WHEN {price} < 10000 THEN 10
             WHEN {price} < 50000 THEN 50 ELSE 100 END
END
"""


def tick_sql(price: str, date: str, market: str) -> str:
    """가격·날짜·시장 컬럼식을 받아 호가 단위 SQL 식을 돌려준다."""
    return PROVISIONAL_TICK_SQL.format(price=price, date=date, market=market)
