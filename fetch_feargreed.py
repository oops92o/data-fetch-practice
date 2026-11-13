"""
코스피 시장의 '공포·탐욕 지수'를 직접 계산해 fear_greed.json 으로 저장합니다.

0에 가까우면 공포(다들 팔고 싶어 함), 100에 가까우면 탐욕(다들 사고 싶어 함)입니다.
여섯 가지를 각각 0~100점으로 매긴 뒤 평균을 냅니다.

  1. 시장 모멘텀     : 코스피가 125일 평균선보다 얼마나 위/아래에 있나
  2. 시장 폭         : 오른 종목이 내린 종목보다 많은가 (최근 10일)
  3. 신고가·신저가   : 60일 최고가를 찍은 종목과 최저가를 찍은 종목 중 어느 쪽이 많은가
  4. 변동성         : 최근 20일 지수가 얼마나 출렁였나 (출렁일수록 공포)
  5. 매수 거래대금   : 거래대금이 오르는 종목에 몰렸나, 내리는 종목에 몰렸나 (최근 5일)
  6. 외국인 수급     : 외국인이 최근 5일 동안 순매수했나

주의: 계산식은 이 파일에서 직접 정한 것입니다. 다른 사이트의 공포·탐욕 지수와
      구성은 비슷해도 식이 달라서 숫자가 똑같이 나오지는 않습니다.
"""

import json
import os
import statistics
import time
from datetime import datetime, timedelta

# ── 내 입맛대로 바꾸는 설정 ─────────────────────────────────────────
SNAP_DAYS = 60        # 종목별 시세를 며칠치 모을지 (신고가·신저가의 '60일'이 이 값)
RANK_DAYS = 250       # "지난 1년 중 몇 번째인가"를 볼 때의 1년 (거래일 기준)
MA_DAYS = 125         # 모멘텀에 쓰는 평균선 길이
VOL_DAYS = 20         # 변동성을 재는 기간
BREADTH_DAYS = 10     # 시장 폭을 평균 내는 기간
MONEY_DAYS = 5        # 매수 거래대금을 합치는 기간
FLOW_DAYS = 5         # 외국인 수급을 합치는 기간
RATIO_LOW, RATIO_HIGH = 0.30, 0.70   # 비율 30%면 0점, 70%면 100점으로 환산
KEEP_HISTORY = 260    # 지난 점수를 며칠치 보관할지
OUT_FILE = "fear_greed.json"

LABELS = [(25, "극단적 공포"), (45, "공포"), (55, "중립"), (75, "탐욕"), (101, "극단적 탐욕")]


# ── 1. KRX에서 가져오기 ─────────────────────────────────────────────
def fetch_all():
    """필요한 원자료 세 가지를 받아 단순한 모양으로 바꿔 돌려줍니다.

      closes : [("20251112", 2531.2), ...]            코스피 종가 (오래된 날 → 최근)
      flows  : [("20251112", -1234.5), ...]           외국인 순매수 (억 원)
      snaps  : [("20261112", {"005930": (종가, 등락률, 거래대금)}), ...]  종목별 시세
    """
    from pykrx import stock

    today = datetime.now()
    end = today.strftime("%Y%m%d")

    # 코스피 지수: 1년 순위 + 125일 평균선을 보려면 넉넉히 2년 가까이 필요
    start = (today - timedelta(days=640)).strftime("%Y%m%d")
    idx = stock.get_index_ohlcv_by_date(start, end, "1001")
    closes = [(d.strftime("%Y%m%d"), float(v)) for d, v in idx["종가"].items() if v > 0]

    # 코스피 전체의 투자자별 순매수. '외국인'이 들어간 열을 찾아 씁니다.
    start = (today - timedelta(days=420)).strftime("%Y%m%d")
    tv = stock.get_market_trading_value_by_date(start, end, "KOSPI")
    col = next(c for c in tv.columns if "외국인" in c)
    flows = [(d.strftime("%Y%m%d"), float(v) / 1e8) for d, v in tv[col].items()]

    # 종목별 하루 시세를 최근 SNAP_DAYS 거래일만큼 (하루에 한 번씩 요청)
    snaps = []
    for d, _ in closes[-SNAP_DAYS:]:
        df = stock.get_market_ohlcv_by_ticker(d, market="KOSPI")
        if df.empty or df["거래대금"].sum() == 0:
            print("  건너뜀 (아직 데이터 없음):", d)
            continue
        df = df[df["거래량"] > 0]      # 거래정지 종목 제외
        snaps.append((d, {
            t: (float(r["종가"]), float(r["등락률"]), float(r["거래대금"]))
            for t, r in df.iterrows()
        }))
        time.sleep(0.7)               # KRX 서버에 부담을 주지 않게 쉬기
    print("종목별 시세", len(snaps), "일치 받음")
    return closes, flows, snaps


# ── 2. 계산 (인터넷 없이도 돌아가는 순수 계산) ──────────────────────
def clamp(x):
    return max(0.0, min(100.0, x))


def rank_score(values):
    """마지막 값이 지난 RANK_DAYS개 값 중 아래에서 몇 %쯤인지 (0~100)."""
    window = values[-RANK_DAYS:]
    last = window[-1]
    return 100.0 * sum(1 for v in window if v <= last) / len(window)


def ratio_score(ratio):
    """0~1 사이 비율을 0~100점으로 (RATIO_LOW → 0점, RATIO_HIGH → 100점)."""
    return clamp((ratio - RATIO_LOW) / (RATIO_HIGH - RATIO_LOW) * 100.0)


def momentum(closes):
    v = [c for _, c in closes]
    gaps = [v[i] / (sum(v[i - MA_DAYS + 1:i + 1]) / MA_DAYS) - 1 for i in range(MA_DAYS - 1, len(v))]
    return rank_score(gaps), f"125일 평균선 대비 {gaps[-1] * 100:+.1f}%"


def volatility(closes):
    v = [c for _, c in closes]
    rets = [v[i] / v[i - 1] - 1 for i in range(1, len(v))]
    vols = [statistics.pstdev(rets[i - VOL_DAYS + 1:i + 1]) for i in range(VOL_DAYS - 1, len(rets))]
    # 출렁임이 클수록 공포이므로 뒤집습니다 (100에서 뺌)
    return 100.0 - rank_score(vols), f"하루 평균 출렁임 {vols[-1] * 100:.2f}%"


def foreign_flow(flows):
    v = [x for _, x in flows]
    sums = [sum(v[i - FLOW_DAYS + 1:i + 1]) for i in range(FLOW_DAYS - 1, len(v))]
    return rank_score(sums), f"최근 {FLOW_DAYS}일 순매수 {sums[-1]:+,.0f}억"


def breadth(snaps):
    ratios = []
    for _, rows in snaps[-BREADTH_DAYS:]:
        up = sum(1 for _, chg, _ in rows.values() if chg > 0)
        down = sum(1 for _, chg, _ in rows.values() if chg < 0)
        if up + down:
            ratios.append(up / (up + down))
    r = sum(ratios) / len(ratios)
    return ratio_score(r), f"오른 종목 비율 {r * 100:.0f}%"


def highs_lows(snaps):
    today = snaps[-1][1]
    highs = lows = 0
    for ticker, (close, _, _) in today.items():
        past = [rows[ticker][0] for _, rows in snaps if ticker in rows]
        if len(past) < len(snaps) * 0.7:      # 상장한 지 얼마 안 된 종목은 제외
            continue
        if close >= max(past):
            highs += 1
        elif close <= min(past):
            lows += 1
    score = 50.0 if highs + lows == 0 else 100.0 * highs / (highs + lows)
    return score, f"신고가 {highs}종목, 신저가 {lows}종목"


def up_money(snaps):
    up = down = 0.0
    for _, rows in snaps[-MONEY_DAYS:]:
        for _, chg, money in rows.values():
            if chg > 0:
                up += money
            elif chg < 0:
                down += money
    r = up / (up + down) if up + down else 0.5
    return ratio_score(r), f"오른 종목 거래대금 비중 {r * 100:.0f}%"


def label_of(score):
    return next(name for limit, name in LABELS if score < limit)


def build(closes, flows, snaps, old_history):
    if len(snaps) < 20:
        raise RuntimeError(f"종목별 시세가 {len(snaps)}일치뿐이라 계산할 수 없습니다.")
    date = snaps[-1][0]
    # 모든 재료를 같은 기준일까지만 쓰도록 맞춥니다.
    closes = [x for x in closes if x[0] <= date]
    flows = [x for x in flows if x[0] <= date]
    if len(closes) < MA_DAYS + 60:
        raise RuntimeError(f"코스피 지수가 {len(closes)}일치뿐이라 계산할 수 없습니다.")

    parts = [
        ("momentum", "시장 모멘텀", momentum(closes)),
        ("breadth", "시장 폭", breadth(snaps)),
        ("highlow", "신고가·신저가", highs_lows(snaps)),
        ("volatility", "변동성", volatility(closes)),
        ("money", "매수 거래대금", up_money(snaps)),
        ("foreign", "외국인 수급", foreign_flow(flows)),
    ]
    components = [
        {"key": k, "name": n, "score": round(s), "detail": detail}
        for k, n, (s, detail) in parts
    ]
    score = round(sum(c["score"] for c in components) / len(components))

    # 지난 점수 기록: 같은 날짜가 있으면 바꾸고, 없으면 덧붙입니다.
    history = [h for h in old_history if h.get("date") != date]
    history.append({"date": date, "score": score})
    history = sorted(history, key=lambda h: h["date"])[-KEEP_HISTORY:]

    last, prev = closes[-1][1], closes[-2][1]
    return {
        "date": date,
        "score": score,
        "label": label_of(score),
        "components": components,
        "kospi": {"close": round(last, 2), "change_pct": round((last / prev - 1) * 100, 2)},
        "history": history,
    }


# ── 3. 실행 ─────────────────────────────────────────────────────────
if __name__ == "__main__":
    old_history = []
    if os.path.exists(OUT_FILE):
        try:
            with open(OUT_FILE, encoding="utf-8") as f:
                old_history = json.load(f).get("history", [])
        except (ValueError, OSError):
            pass

    closes, flows, snaps = fetch_all()
    result = build(closes, flows, snaps, old_history)

    with open(OUT_FILE, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=1)
    print("저장 완료:", result["date"], result["score"], result["label"])
    for c in result["components"]:
        print("  ", c["name"], c["score"], "-", c["detail"])
