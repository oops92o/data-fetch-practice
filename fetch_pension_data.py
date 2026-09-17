"""
연기금 순매수 상위 종목 + 연속 매집일수를 뽑아서 data.json 으로 저장합니다.
GitHub Actions가 이 스크립트를 매일 대신 실행해줍니다.

준비물: pip install pykrx

금액 기준(원) 데이터를 쓰고, "연기금" 투자자 구분을 그대로 사용합니다.
(연기금 계좌는 프로그램매매/차익거래를 거의 하지 않아서,
 기관합계보다 이 구분이 이미 '진짜 기관 수급'에 가깝습니다.)
"""

import json
from datetime import datetime, timedelta

from pykrx import stock

# ── 1. 오늘 기준 연기금 순매수 상위/하위 ────────────────────────────
target_date = stock.get_nearest_business_day_in_a_week()

df = stock.get_market_net_purchases_of_equities(
    target_date, target_date, market="ALL", investor="연기금"
)
df_sorted = df.sort_values(by="순매수거래대금", ascending=False)


def to_list(rows):
    result = []
    for ticker, row in rows.iterrows():
        result.append(
            {
                "ticker": ticker,
                "name": row["종목명"],
                "amount_eok": round(row["순매수거래대금"] / 1e8, 1),  # 억원 단위
            }
        )
    return result


top_buy = to_list(df_sorted.head(10))
top_sell = to_list(df_sorted.tail(10).sort_values(by="순매수거래대금"))


# ── 2. 연속 매집일수 계산 (top_buy 종목만) ──────────────────────────
# 최근 20거래일 동안, 오늘부터 거꾸로 세면서 며칠 연속 연기금 순매수(+)였는지 확인
lookback_start = (
    datetime.strptime(target_date, "%Y%m%d") - timedelta(days=40)
).strftime("%Y%m%d")  # 주말/공휴일 감안해 넉넉히 40일 전부터 조회


def get_streak(ticker: str) -> int:
    try:
        hist = stock.get_market_trading_value_by_date(
            lookback_start, target_date, ticker, detail=True
        )
    except Exception:
        return 0

    if "연기금" not in hist.columns:
        return 0

    series = hist["연기금"].sort_index(ascending=False)  # 최신 날짜부터
    streak = 0
    for value in series:
        if value > 0:
            streak += 1
        else:
            break
    return streak


for item in top_buy:
    item["streak_days"] = get_streak(item["ticker"])


# ── 3. 저장 ─────────────────────────────────────────────────────────
data = {
    "date": target_date,
    "top_buy": top_buy,
    "top_sell": top_sell,
}

with open("data.json", "w", encoding="utf-8") as f:
    json.dump(data, f, ensure_ascii=False, indent=2)

print("저장 완료:", target_date)
