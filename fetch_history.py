"""
최근 20거래일의 연기금 순매수를 모아서 두 가지를 계산하고 history.json 으로 저장합니다.

  1. 기간 누적  : 최근 5일(1주) / 20일(1달) 동안 합쳐서 많이 산·판 종목
  2. 방향 전환  : 며칠 팔다가 오늘 매수로 바뀐 종목 (그 반대도)

기존 fetch_pension_data.py 와 data.json 은 건드리지 않습니다.
"""

import json
import time
from datetime import datetime, timedelta

# ── 내 입맛대로 바꾸는 설정 ─────────────────────────────────────────
DAYS = 20             # 몇 거래일치를 모을지
TOP_N = 10            # 순위에 몇 종목까지 보여 줄지
TURN_MIN_DAYS = 5     # 방향 전환: 그 전에 최소 며칠 연속 반대였어야 하는지
TURN_MIN_EOK = 1.0    # 방향 전환: 오늘 금액이 최소 몇 억이어야 하는지 (잔돈 걸러내기)
INVESTOR = "연기금"
EOK = 1e8             # 1억 원


# ── 1. KRX에서 가져오기 ─────────────────────────────────────────────
def get_business_days(n):
    """삼성전자의 일별 거래 기록에서 최근 n개 거래일 날짜를 뽑습니다."""
    from pykrx import stock

    today = datetime.now()
    start = (today - timedelta(days=n * 2 + 20)).strftime("%Y%m%d")
    end = today.strftime("%Y%m%d")
    hist = stock.get_market_trading_value_by_date(start, end, "005930")
    days = [d.strftime("%Y%m%d") for d in hist.index]
    return days[-n:]


def fetch_daily(days):
    """날짜마다 '종목코드 → 순매수 금액(억)' 표를 받아 옵니다.

    돌려주는 값
      daily : {"20261112": {"005930": 1810.2, ...}, ...}
      names : {"005930": "삼성전자", ...}
    """
    from pykrx import stock

    daily, names = {}, {}
    for d in days:
        df = stock.get_market_net_purchases_of_equities(d, d, market="ALL", investor=INVESTOR)
        if df.empty or df["순매수거래대금"].abs().sum() == 0:
            print("  건너뜀 (아직 데이터 없음):", d)
            continue
        daily[d] = {t: float(v) / EOK for t, v in df["순매수거래대금"].items()}
        names.update(df["종목명"].to_dict())
        print("  받음:", d, len(df), "종목")
        time.sleep(1)  # KRX 서버에 부담을 주지 않게 1초 쉬기
    return daily, names


# ── 2. 계산 (여기는 인터넷 없이도 돌아가는 순수 계산) ────────────────
def cumulative(daily, days, names, top_n=TOP_N):
    """days 에 든 날짜들의 금액을 종목별로 더해 상위/하위를 뽑습니다."""
    total = {}
    for d in days:
        for ticker, v in daily[d].items():
            total[ticker] = total.get(ticker, 0.0) + v

    ranked = sorted(total.items(), key=lambda kv: kv[1], reverse=True)

    def pack(pairs):
        return [
            {"ticker": t, "name": names.get(t, t), "amount_eok": round(v, 1)}
            for t, v in pairs
        ]

    buy = [p for p in ranked[:top_n] if p[1] > 0]
    sell = [p for p in reversed(ranked[-top_n:]) if p[1] < 0]
    return {"days": len(days), "buy": pack(buy), "sell": pack(sell)}


def find_turns(daily, days, names):
    """오늘 방향이 바뀐 종목을 찾습니다.

    오늘 값의 부호와 '반대'인 날이 어제부터 거꾸로 며칠 연속이었는지 셉니다.
    """
    today, earlier = days[-1], days[-2::-1]  # earlier: 어제 → 그제 → ... 순서
    to_buy, to_sell = [], []

    for ticker, v in daily[today].items():
        if abs(v) < TURN_MIN_EOK:
            continue
        prev_days = 0
        for d in earlier:
            past = daily[d].get(ticker, 0.0)
            if past * v < 0:      # 곱이 음수 = 부호가 서로 반대
                prev_days += 1
            else:
                break
        if prev_days < TURN_MIN_DAYS:
            continue
        item = {
            "ticker": ticker,
            "name": names.get(ticker, ticker),
            "prev_days": prev_days,
            "capped": prev_days == len(earlier),  # 가진 데이터 끝까지 이어졌으면 '이상'
            "amount_eok": round(v, 1),
        }
        (to_buy if v > 0 else to_sell).append(item)

    order = lambda x: (-x["prev_days"], -abs(x["amount_eok"]))
    return {"to_buy": sorted(to_buy, key=order)[:TOP_N],
            "to_sell": sorted(to_sell, key=order)[:TOP_N]}


def build(daily, names):
    days = sorted(daily.keys())
    if len(days) < TURN_MIN_DAYS + 1:
        raise RuntimeError(f"데이터가 {len(days)}일치뿐이라 계산할 수 없습니다.")
    return {
        "date": days[-1],
        "days_collected": len(days),
        "cum": {
            "week": cumulative(daily, days[-5:], names),
            "month": cumulative(daily, days, names),
        },
        "turn": find_turns(daily, days, names),
        "settings": {"turn_min_days": TURN_MIN_DAYS, "turn_min_eok": TURN_MIN_EOK},
    }


# ── 3. 실행 ─────────────────────────────────────────────────────────
if __name__ == "__main__":
    days = get_business_days(DAYS)
    if not days:
        raise RuntimeError("거래일 목록을 받지 못했습니다. KRX 로그인을 확인하세요.")
    print("거래일", len(days), "개:", days[0], "~", days[-1])

    daily, names = fetch_daily(days)
    result = build(daily, names)

    with open("history.json", "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    print("저장 완료:", result["date"], "/", result["days_collected"], "일치")
