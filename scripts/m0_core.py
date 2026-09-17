"""M0 핵심 3개 확인: 기준가 역산 계수, 대비 방향 표현, 상장폐지 종목 포함.

사용 (저장소 루트, PowerShell):
    .\\.venv\\Scripts\\python.exe scripts\\m0_core.py

원문은 data/raw/{service}/{basDd}.json에 저장되고, 이미 있으면 다시 호출하지 않는다.
종목코드는 기억에 의존한 값이므로 코드와 종목명으로 함께 찾아 출력한다.
"""

import json
import sys

from krx_backtester.data.krx_client import DEFAULT_RAW_DIR, fetch


def load(service: str, bas_dd: str) -> list[dict]:
    path = DEFAULT_RAW_DIR / service / f"{bas_dd}.json"
    if not path.exists():
        r = fetch(service, bas_dd)
        print(f"[call] {service} {bas_dd} -> HTTP {r.status_code}")
        if r.status_code != 200:
            print(r.text)
            sys.exit(1)
    body = json.loads(path.read_text(encoding="utf-8"))
    if "OutBlock_1" not in body:
        print(f"[stop] {service} {bas_dd}: OutBlock_1 없음: {body}")
        sys.exit(1)
    return body["OutBlock_1"]


def find(rows: list[dict], code: str, name: str) -> dict | None:
    """코드로 찾고, 없거나 이름이 다르면 이름 포함 행을 함께 출력한다."""
    by_code = next((x for x in rows if x["ISU_CD"] == code), None)
    if by_code and name in by_code["ISU_NM"]:
        return by_code
    by_name = [x for x in rows if name in x["ISU_NM"]]
    print(f"  [주의] 코드 {code} 행: {by_code and by_code['ISU_NM']}, 이름 '{name}' 포함 행: {[(x['ISU_CD'], x['ISU_NM']) for x in by_name]}")
    return by_name[0] if len(by_name) == 1 else None


def num(s: str) -> float | None:
    try:
        return float(s.replace(",", ""))
    except ValueError:
        return None


def check_factor() -> None:
    print("\n== 1. 기준가 역산 보정식 (삼성전자)")
    rows = {d: find(load("stk_bydd_trd", d), "005930", "삼성전자") for d in ("20180427", "20180503", "20180504")}
    for d, x in rows.items():
        print(d, x)
    cur = rows["20180504"]
    base = num(cur["TDD_CLSPRC"]) - num(cur["CMPPREVDD_PRC"])
    for d in ("20180503", "20180427"):
        prev = rows[d]
        if prev and num(prev["TDD_CLSPRC"]):
            factor = base / num(prev["TDD_CLSPRC"])
            print(f"기준가 = {base:g}, 전일 종가({d}) = {prev['TDD_CLSPRC']}, 계수 = {factor:.6f}, 1/50 = {1/50:.6f}")


def check_direction() -> None:
    print("\n== 2. 대비 방향 표현 (20180504 유가증권 전체)")
    rows = load("stk_bydd_trd", "20180504")
    print("필드 목록:", list(rows[0].keys()))
    down = [x for x in rows if (num(x["FLUC_RT"]) or 0) < 0]
    print(f"전체 {len(rows)}행, FLUC_RT < 0 인 행 {len(down)}개. 예시 3개:")
    for x in down[:3]:
        print({k: x[k] for k in ("ISU_CD", "ISU_NM", "TDD_CLSPRC", "CMPPREVDD_PRC", "FLUC_RT")})
    print("하락 행 중 CMPPREVDD_PRC < 0:", sum((num(x["CMPPREVDD_PRC"]) or 0) < 0 for x in down), "/", len(down))
    mismatch = 0
    for x in rows:
        close, diff, rt = num(x["TDD_CLSPRC"]), num(x["CMPPREVDD_PRC"]), num(x["FLUC_RT"])
        base = None if close is None or diff is None else close - diff
        if base and rt is not None and abs(round((close / base - 1) * 100, 2) - rt) > 0.011:
            mismatch += 1
    print(f"FLUC_RT와 (종가 / (종가 − 대비) − 1) × 100 불일치 행: {mismatch}")


def check_delisted() -> None:
    print("\n== 3. 상장폐지 종목 포함 (한진해운, 폐지 2017-03-07)")
    for d in ("20160601", "20170306", "20170307"):
        print(d, find(load("stk_bydd_trd", d), "117930", "한진해운"))


def main() -> None:
    check_factor()
    check_direction()
    check_delisted()


if __name__ == "__main__":
    main()
