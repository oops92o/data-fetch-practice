"""
연기금 순매수 상위 종목을 뽑아서 data.json 으로 저장합니다.
GitHub Actions가 이 스크립트를 매일 대신 실행해줄 거라서,
아이폰이나 로컬 컴퓨터에 파이썬이 없어도 됩니다.

준비물: pip install pykrx
"""

import json
from pykrx import stock

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


data = {
    "date": target_date,
    "top_buy": to_list(df_sorted.head(10)),
    "top_sell": to_list(df_sorted.tail(10).sort_values(by="순매수거래대금")),
}

with open("data.json", "w", encoding="utf-8") as f:
    json.dump(data, f, ensure_ascii=False, indent=2)

print("저장 완료:", target_date)
