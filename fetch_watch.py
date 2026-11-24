"""
watchlist.json 에 적은 내 종목들을 매일 추적해서 watch.json 으로 저장합니다.
대시보드 맨 위 '내 종목 트래킹' 구역이 이 파일을 씁니다.

종목마다 모으는 것
  - 주가: 최근 20거래일 종가와 등락률 (pykrx)
  - 수급: 날짜별 외국인·연기금 순매수 (pykrx, 로그인은 KRX_ID / KRX_PW)
  - 대차잔고: lending.json 에서 오늘 값을 가져와, 날마다 쌓아 둬요 (그래서 며칠 지나면 추이가 생겨요)
  - 수급 포착: signals.json 에 걸린 규칙이 있으면 함께 표시

── 상태를 이렇게 정해요 ───────────────────────────────────────────
파는 힘 3가지를 봐요: ① 외국인 5일 순매도 ② 연기금 5일 순매도 ③ 대차잔고 증가(공매도 대기)
  3개 다 해당 → '파는 힘 우세'   1~2개 → '엇갈림'   0개 → '사는 힘 우세'

회복 신호 체크리스트 (파는 힘이 풀리는지 하나씩 확인해요)
  ① 외국인이 오늘 순매수로 돌아섰나
  ② 연기금이 오늘 순매수로 돌아섰나
  ③ 대차잔고가 최근 5일 동안 줄었나
  ④ 종가가 5일 평균 위로 올라섰나
"""

import json
import os
from datetime import datetime, timedelta

DAYS = 20          # 몇 거래일치를 보여 줄지
VOL_DAYS = 60      # 변동성은 이만큼의 거래일로도 계산해요(20일과 60일 두 가지)
KEEP_LEND = 60     # 대차잔고 기록을 며칠치까지 쌓아 둘지
EOK = 1e8


def load(path, default=None):
    if not os.path.exists(path):
        return default
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except ValueError:      # 파일이 깨져 있으면 없는 셈 쳐요
        print("  읽기 실패, 건너뜀:", path)
        return default


def last_closed_day():
    """수급 확정치가 나오는 저녁 6시(한국시간) 전이면 오늘은 '아직 진행 중'으로 보고 빼요.
    GitHub 예약 실행이 늦어져 장중에 돌면, 반쪽짜리 오늘 숫자가 들어가는 걸 막으려는 거예요."""
    from datetime import timezone
    now = datetime.now(timezone(timedelta(hours=9)))
    if now.hour < 18:
        now -= timedelta(days=1)
    return now.strftime("%Y%m%d")


def realized_vol(closes, n):
    """최근 n일 일간 수익률로 계산한 연간 변동성(%). VKOSPI와 같은 단위라 나란히 비교할 수 있어요."""
    import math
    c = closes[-(n + 1):]
    if len(c) < n + 1:
        return None
    rets = [math.log(c[i] / c[i - 1]) for i in range(1, len(c))]
    mean = sum(rets) / len(rets)
    var = sum((r - mean) ** 2 for r in rets) / (len(rets) - 1)
    return round(math.sqrt(var) * math.sqrt(250) * 100, 1)


def side(v, flat=0):
    return 1 if v > flat else (-1 if v < -flat else 0)


def streak(values):
    """맨 끝(최근)부터 같은 부호가 며칠째인지. 순매수면 +, 순매도면 −"""
    if not values or values[-1] == 0:
        return 0
    s, n = side(values[-1]), 0
    for v in reversed(values):
        if side(v) == s:
            n += 1
        else:
            break
    return n * s


def fetch_stock(ticker, end_day):
    """(최근 DAYS일 행 목록, 변동성 계산용 종가 목록)을 돌려줘요."""
    from pykrx import stock

    end = datetime.strptime(end_day, "%Y%m%d")
    start = (end - timedelta(days=VOL_DAYS * 2 + 20)).strftime("%Y%m%d")

    price_all = stock.get_market_ohlcv_by_date(start, end_day, ticker)
    price = price_all.tail(DAYS)
    flow = stock.get_market_trading_value_by_date(start, end_day, ticker, detail=True)

    rows = []
    for idx, p in price.iterrows():
        f = flow.loc[idx] if idx in flow.index else None
        foreign = (f.get("외국인", 0) + f.get("기타외국인", 0)) if f is not None else 0
        pension = f.get("연기금", 0) if f is not None else 0
        rows.append({
            "date": idx.strftime("%Y%m%d"),
            "close": int(p["종가"]),
            "chg": round(float(p.get("등락률", 0)), 2),
            "foreign": round(float(foreign) / EOK, 1),   # float(): pandas 숫자를 JSON 으로 저장할 수 있게
            "pension": round(float(pension) / EOK, 1),
        })
    return rows, [float(c) for c in price_all["종가"].tolist()]


def kospi_vol(end_day):
    """코스피 지수의 20일 실현 변동성(%). 종목 변동성이 시장의 몇 배인지 보려고요."""
    from pykrx import stock
    end = datetime.strptime(end_day, "%Y%m%d")
    start = (end - timedelta(days=60)).strftime("%Y%m%d")
    df = stock.get_index_ohlcv_by_date(start, end_day, "1001")   # 1001 = 코스피
    return realized_vol([float(c) for c in df["종가"].tolist()], 20)


def move_label(chg, vol20):
    """오늘 등락이 평소 하루 폭(20일 변동성 ÷ 16)의 몇 배인지로 말해요."""
    if not vol20:
        return None, None
    daily = vol20 / 15.8
    ratio = abs(chg) / daily if daily else 0
    label = "평소 범위" if ratio < 1 else ("큰 편" if ratio < 2 else "이례적")
    return round(ratio, 1), label


def main():
    wl = load("watchlist.json", {"stocks": []})
    lend = load("lending.json")
    sig = load("signals.json", {"up": [], "down": []})
    old = load("watch.json", {"stocks": []})
    old_lend = {s["ticker"]: s.get("lend_hist", []) for s in old.get("stocks", [])}

    end_day = last_closed_day()
    print("기준: ", end_day, "까지 확정된 날만 써요")
    try:
        mkt_vol = kospi_vol(end_day)
    except Exception as e:
        print("  코스피 변동성 계산 실패(건너뜀):", e)
        mkt_vol = None

    out = []
    for w in wl["stocks"]:
        t = w["ticker"]
        print("수집:", t, w.get("name"))
        rows, all_closes = fetch_stock(t, end_day)
        if not rows:
            print("  자료 없음, 건너뜀")
            continue
        fr = [r["foreign"] for r in rows]
        pe = [r["pension"] for r in rows]
        closes = [r["close"] for r in rows]

        # 대차잔고: 오늘 값을 기록에 쌓아요 (같은 날짜면 덮어쓰기)
        ls = None
        hist = [h for h in old_lend.get(t, [])]
        if lend and t in lend["stocks"]:
            ls = dict(zip(lend["fields"], lend["stocks"][t]))
            hist = [h for h in hist if h["date"] != lend["date"]]
            hist.append({"date": lend["date"], "bal_eok": ls["bal_eok"]})
            hist = sorted(hist, key=lambda h: h["date"])[-KEEP_LEND:]

        # 수급 포착 규칙
        rules, rule_side = [], None
        for s_name, items in (("up", sig.get("up", [])), ("down", sig.get("down", []))):
            for x in items:
                if x["ticker"] == t:
                    rules, rule_side = x["rules"], s_name

        f5, p5 = round(sum(fr[-5:]), 1), round(sum(pe[-5:]), 1)
        lend_up = bool(ls and ls.get("chg5") is not None and ls["chg5"] > 0)
        pressure = int(f5 < 0) + int(p5 < 0) + int(lend_up)
        status = {3: ("sell", "파는 힘 우세"), 0: ("buy", "사는 힘 우세")}.get(pressure, ("mixed", "엇갈림"))

        ma5 = sum(closes[-5:]) / min(5, len(closes))
        checks = [
            {"label": "외국인 순매수 전환", "ok": bool(fr[-1] > 0)},
            {"label": "연기금 순매수 전환", "ok": bool(pe[-1] > 0)},
            {"label": "대차잔고 5일 감소", "ok": bool(ls and ls.get("chg5") is not None and ls["chg5"] < 0)},
            {"label": "종가가 5일 평균 위", "ok": bool(closes[-1] > ma5)},
        ]

        vol20, vol60 = realized_vol(all_closes, 20), realized_vol(all_closes, VOL_DAYS)
        ratio, mlabel = move_label(rows[-1]["chg"], vol20)
        vol = {"v20": vol20, "v60": vol60, "daily": round(vol20 / 15.8, 1) if vol20 else None,
               "kospi20": mkt_vol, "vs_kospi": round(vol20 / mkt_vol, 1) if vol20 and mkt_vol else None,
               "move_ratio": ratio, "move_label": mlabel}

        out.append({
            "ticker": t, "vol": vol, "name": w.get("name", t), "note": w.get("note", ""),
            "close": closes[-1], "chg": rows[-1]["chg"],
            "ret20": round((closes[-1] / closes[0] - 1) * 100, 1) if closes[0] else None,
            "status": status[0], "status_label": status[1], "pressure": int(pressure),
            "checks": checks,
            "foreign": {"d1": fr[-1], "d5": f5, "d20": round(sum(fr), 1), "streak": streak(fr)},
            "pension": {"d1": pe[-1], "d5": p5, "d20": round(sum(pe), 1), "streak": streak(pe)},
            "lend": ({"date": lend["date"], "bal_eok": ls["bal_eok"], "chg5": ls["chg5"], "chg20": ls["chg20"],
                      "chg5_eok": ls.get("chg5_eok"), "signal": ls["signal"]} if ls else None),
            "lend_hist": hist,
            "rules": rules, "rule_side": rule_side,
            "days": rows,
        })
        print(f"  {status[1]} (파는 힘 {pressure}/3), 회복 신호 {sum(c['ok'] for c in checks)}/4")

    data = {"version": 1, "date": out[0]["days"][-1]["date"] if out else None, "stocks": out}
    # 임시 파일에 다 쓴 뒤 바꿔 끼워요. 중간에 실패해도 기존 watch.json 이 깨지지 않게요.
    with open("watch.json.tmp", "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    os.replace("watch.json.tmp", "watch.json")
    print("저장 완료:", len(out), "종목")


if __name__ == "__main__":
    main()
