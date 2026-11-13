"""
주요 지수(코스피, 코스닥, 코스피 200)와 코스피 업종별 등락률을 받아 market.json 으로 저장합니다.

등락률은 KRX가 주는 값을 그대로 쓰지 않고, 날짜별 종가를 받아서 직접 계산합니다.
  1일 등락 = 오늘 종가 ÷ 어제 종가 − 1
  1주 등락 = 오늘 종가 ÷ 5거래일 전 종가 − 1
"""

import json
import time
from datetime import datetime, timedelta

# ── 내 입맛대로 바꾸는 설정 ─────────────────────────────────────────
WEEK = 5   # '1주'를 몇 거래일로 볼지

# 맨 위에 보여 줄 지수. (화면 이름, 시장, KRX에서 쓰는 이름 후보들)
MAIN_INDICES = [
    ("코스피", "KOSPI", ["코스피"]),
    ("코스닥", "KOSDAQ", ["코스닥", "코스닥지수"]),
    ("코스피 200", "KOSPI", ["코스피 200", "코스피200"]),
]

# 업종 목록에서 뺄 이름. 업종이 아니라 여러 업종을 묶은 큰 분류라서 뺍니다.
SKIP_SECTORS = {"제조업", "제조"}


# ── 1. KRX에서 가져오기 ─────────────────────────────────────────────
def fetch_all():
    """오늘 / 어제 / 1주 전, 세 날짜의 지수 종가표를 받아 돌려줍니다.

      days   : ["20261105", "20261112", "20261113"]   (1주 전, 어제, 오늘)
      tables : {"KOSPI": {날짜: {지수명: 종가}}, "KOSDAQ": {...}}
    """
    from pykrx import stock

    today = datetime.now()
    start = (today - timedelta(days=30)).strftime("%Y%m%d")
    idx = stock.get_index_ohlcv_by_date(start, today.strftime("%Y%m%d"), "1001")
    all_days = [d.strftime("%Y%m%d") for d, v in idx["종가"].items() if v > 0]
    if len(all_days) < WEEK + 1:
        raise RuntimeError(f"거래일이 {len(all_days)}일치뿐입니다. KRX 로그인을 확인하세요.")
    days = [all_days[-1 - WEEK], all_days[-2], all_days[-1]]

    tables = {"KOSPI": {}, "KOSDAQ": {}}
    for market in tables:
        for d in days:
            df = stock.get_index_ohlcv_by_ticker(d, market)
            tables[market][d] = {str(name).strip(): float(v) for name, v in df["종가"].items() if v > 0}
            print("  받음:", market, d, len(tables[market][d]), "개 지수")
            time.sleep(0.7)
    return days, tables


# ── 2. 계산 (인터넷 없이도 돌아가는 순수 계산) ──────────────────────
def pct(now, before):
    return round((now / before - 1) * 100, 2)


def is_sector(name):
    """업종 지수인지 가려냅니다. '코스피 200'처럼 '코스피'로 시작하는 건 업종이 아닙니다."""
    return not name.startswith("코스피") and name not in SKIP_SECTORS


def build(days, tables):
    week_ago, yesterday, today = days

    indices = []
    for label, market, candidates in MAIN_INDICES:
        t = tables[market]
        name = next((c for c in candidates if c in t[today] and c in t[yesterday]), None)
        if name is None:
            print("  찾지 못한 지수:", label)
            continue
        indices.append({
            "name": label,
            "close": round(t[today][name], 2),
            "d1": pct(t[today][name], t[yesterday][name]),
        })

    sectors = []
    k = tables["KOSPI"]
    for name, close in k[today].items():
        if not is_sector(name) or name not in k[yesterday]:
            continue
        item = {"name": name, "d1": pct(close, k[yesterday][name])}
        if name in k[week_ago]:
            item["w1"] = pct(close, k[week_ago][name])
        sectors.append(item)
    sectors.sort(key=lambda s: s["d1"], reverse=True)

    return {"date": today, "indices": indices, "sectors": sectors}


# ── 3. 실행 ─────────────────────────────────────────────────────────
if __name__ == "__main__":
    days, tables = fetch_all()
    result = build(days, tables)
    if not result["indices"] and not result["sectors"]:
        raise RuntimeError("지수도 업종도 찾지 못했습니다. KRX 응답 형식이 바뀌었을 수 있습니다.")

    with open("market.json", "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=1)
    print("저장 완료:", result["date"], "/ 지수", len(result["indices"]), "개, 업종", len(result["sectors"]), "개")
    print("  업종:", ", ".join(s["name"] for s in result["sectors"]))
