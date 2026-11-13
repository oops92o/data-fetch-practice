"""
최근 20거래일의 연기금·외국인 순매수를 모아서 history.json 으로 저장합니다.

투자 주체(연기금, 외국인)마다 세 가지를 계산합니다.
  1. 오늘 상위  : 오늘 많이 산·판 종목과 며칠째 이어지는지
  2. 기간 누적  : 최근 5일(1주) / 20일(1달) 동안 합쳐서 많이 산·판 종목
  3. 방향 전환  : 며칠 팔다가 오늘 매수로 바뀐 종목 (그 반대도)
그리고 둘이 같은 날 함께 산 종목(동시 매수)도 뽑습니다.

기존 fetch_pension_data.py 와 data.json 은 건드리지 않습니다 (위젯이 계속 씁니다).
"""

import json
import time
from datetime import datetime, timedelta

# ── 내 입맛대로 바꾸는 설정 ─────────────────────────────────────────
DAYS = 20             # 몇 거래일치를 모을지
TOP_N = 10            # 순위에 몇 종목까지 보여 줄지
TURN_MIN_DAYS = 5     # 방향 전환: 그 전에 최소 며칠 연속 반대였어야 하는지
TURN_MIN_EOK = 1.0    # 방향 전환: 오늘 금액이 최소 몇 억이어야 하는지 (잔돈 걸러내기)
EOK = 1e8             # 1억 원

# 화면에 보여 줄 투자 주체. "이름표": "KRX에서 쓰는 이름"
# 예) "inst": "기관합계" 한 줄을 더하면 기관 탭도 생깁니다.
INVESTORS = {
    "pension": "연기금",
    "foreign": "외국인",
}


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


def fetch_daily(days, investor):
    """한 투자 주체에 대해, 날짜마다 '종목코드 → 순매수 금액(억)' 표를 받아 옵니다.

    돌려주는 값
      daily : {"20261112": {"005930": 1810.2, ...}, ...}
      names : {"005930": "삼성전자", ...}
    """
    from pykrx import stock

    daily, names = {}, {}
    for d in days:
        df = stock.get_market_net_purchases_of_equities(d, d, market="ALL", investor=investor)
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


def today_top(daily, days, names, top_n=TOP_N):
    """오늘 많이 산·판 종목과, 같은 방향이 오늘까지 며칠째인지 셉니다."""
    today = days[-1]
    ranked = sorted(daily[today].items(), key=lambda kv: kv[1], reverse=True)

    def streak(ticker, v):
        n = 0
        for d in reversed(days):           # 오늘 → 어제 → 그제 ...
            if daily[d].get(ticker, 0.0) * v > 0:   # 곱이 양수 = 같은 방향
                n += 1
            else:
                break
        return n

    def pack(pairs):
        return [
            {"ticker": t, "name": names.get(t, t), "amount_eok": round(v, 1),
             "streak_days": streak(t, v)}
            for t, v in pairs
        ]

    buy = [p for p in ranked[:top_n] if p[1] > 0]
    sell = [p for p in reversed(ranked[-top_n:]) if p[1] < 0]
    return {"buy": pack(buy), "sell": pack(sell)}


def find_twins(daily_a, daily_b, day, names, top_n=TOP_N):
    """같은 날 두 주체가 모두 순매수한 종목을, 합친 금액이 큰 순서로 뽑습니다."""
    a, b = daily_a[day], daily_b[day]
    both = [(t, a[t], b[t]) for t in a if a[t] > 0 and b.get(t, 0.0) > 0]
    both.sort(key=lambda x: x[1] + x[2], reverse=True)
    return [
        {"ticker": t, "name": names.get(t, t),
         "pension_eok": round(va, 1), "foreign_eok": round(vb, 1)}
        for t, va, vb in both[:top_n]
    ]


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


def build_one(daily, names, label):
    """한 투자 주체의 결과 묶음을 만듭니다."""
    days = sorted(daily.keys())
    if len(days) < TURN_MIN_DAYS + 1:
        raise RuntimeError(f"{label} 데이터가 {len(days)}일치뿐이라 계산할 수 없습니다.")
    return {
        "label": label,
        "date": days[-1],
        "days_collected": len(days),
        "today": today_top(daily, days, names),
        "cum": {
            "week": cumulative(daily, days[-5:], names),
            "month": cumulative(daily, days, names),
        },
        "turn": find_turns(daily, days, names),
    }


def build(all_daily, names):
    """all_daily: {"pension": {날짜: {종목: 금액}}, "foreign": {...}}"""
    investors = {key: build_one(all_daily[key], names, INVESTORS[key]) for key in all_daily}

    # 동시 매수는 연기금·외국인의 최신 날짜가 같을 때만 계산합니다.
    twin = []
    p, f = investors.get("pension"), investors.get("foreign")
    if p and f and p["date"] == f["date"]:
        twin = find_twins(all_daily["pension"], all_daily["foreign"], p["date"], names)

    first = next(iter(investors.values()))
    return {
        "version": 2,
        "date": first["date"],
        "investors": investors,
        "twin": twin,
        "settings": {"turn_min_days": TURN_MIN_DAYS, "turn_min_eok": TURN_MIN_EOK},
    }


# ── 3. 실행 ─────────────────────────────────────────────────────────
if __name__ == "__main__":
    days = get_business_days(DAYS)
    if not days:
        raise RuntimeError("거래일 목록을 받지 못했습니다. KRX 로그인을 확인하세요.")
    print("거래일", len(days), "개:", days[0], "~", days[-1])

    all_daily, names = {}, {}
    for key, krx_name in INVESTORS.items():
        print(krx_name, "받는 중")
        all_daily[key], found = fetch_daily(days, krx_name)
        names.update(found)

    result = build(all_daily, names)

    with open("history.json", "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    print("저장 완료:", result["date"], "/", ", ".join(INVESTORS.values()))
