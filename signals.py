"""
history.json(연기금·외국인 수급) + lending.json(대차잔고)을 겹쳐 보고,
정해 둔 규칙에 맞는 종목을 signals.json 으로 저장합니다.
인터넷 없이 두 파일만 읽어서 계산해요. 대차잔고 워크플로 마지막에 자동으로 돌아요.

── 포착 규칙 ──────────────────────────────────────────────────────
상승 쪽 (사는 힘과 갚는 힘이 같은 방향)
  A. 매수 + 상환      : 연기금·외국인 1주 순매수 상위인데, 대차잔고가 줄고 있음
  B. 동시 매수 + 상환 : 오늘 연기금·외국인이 함께 샀는데, 대차잔고가 줄고 있음
  C. 매수 전환 + 상환 : 며칠 팔다가 오늘 매수로 돌아섰는데, 대차잔고가 줄고 있음
주의 쪽
  D. 대기 우세        : 순매수 상위인데 5일 대차 증가액이 5일 순매수의 3배 이상
  E. 매도 + 대기 증가 : 1주 순매도 상위인데, 대차잔고도 늘고 있음 (파는 힘이 둘)
  F. 하루짜리 매수    : 오늘 순매수 상위인데 5일로 보면 순매도
같은 종목이 여러 규칙에 걸리면 한 줄로 합치고, 걸린 개수가 많을수록 위에 보여요.
"""

import json

VS_RATIO = 3.0        # D 규칙: 대차 증가액이 순매수의 몇 배 이상이면 '대기 우세'로 볼지
COVER = {"cover", "cover2"}
SHORT = {"short", "short2"}


def load(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def main():
    hist = load("history.json")
    lend = load("lending.json")
    fields = lend["fields"]

    def stock(t):
        v = lend["stocks"].get(t)
        return dict(zip(fields, v)) if v else None

    found = {}   # 종목코드 → {"name", "up": [...], "down": [...], "week": {주체: 금액}}

    def hit(t, name, side, rule, who_label=None, week=None):
        x = found.setdefault(t, {"ticker": t, "name": name, "up": [], "down": [], "week": {}})
        label = (who_label + " " if who_label else "") + rule
        if label not in x[side]:
            x[side].append(label)
        if who_label and week is not None:
            x["week"][who_label] = week

    for key, inv in hist["investors"].items():
        who = inv["label"]
        week_buy = {x["ticker"]: x for x in inv["cum"]["week"]["buy"]}
        week_sell = {x["ticker"]: x for x in inv["cum"]["week"]["sell"]}

        # A. 1주 순매수 상위 + 상환
        for t, x in week_buy.items():
            s = stock(t)
            if s and s["signal"] in COVER:
                hit(t, x["name"], "up", "1주 매수 + 상환", who, x["amount_eok"])

        # C. 매수 전환 + 상환
        for x in inv["turn"]["to_buy"]:
            s = stock(x["ticker"])
            if s and s["signal"] in COVER:
                hit(x["ticker"], x["name"], "up", f"{x['prev_days']}일 매도 후 매수 전환 + 상환", who)

        # D. 대기 우세 / F. 하루짜리 매수 (오늘 상위 + 1주 상위)
        cands = {x["ticker"]: (x["name"], x.get("week_eok")) for x in inv["today"]["buy"]}
        for t, x in week_buy.items():
            cands.setdefault(t, (x["name"], x["amount_eok"]))
        for t, (name, wk) in cands.items():
            s = stock(t)
            if wk is not None and wk < 0:
                hit(t, name, "down", "오늘만 매수 (5일은 순매도)", who, wk)
                continue
            if not s or s["signal"] not in SHORT or wk is None or not s.get("chg5_eok"):
                continue
            if s["chg5_eok"] >= VS_RATIO * max(wk, 0.1):
                hit(t, name, "down", f"매수보다 대차 증가가 {s['chg5_eok'] / max(wk, 0.1):.1f}배", who, wk)

        # E. 1주 순매도 상위 + 대차 증가
        for t, x in week_sell.items():
            s = stock(t)
            if s and s["signal"] in SHORT:
                hit(t, x["name"], "down", "1주 매도 + 대차 증가", who, x["amount_eok"])

    # B. 동시 매수 + 상환
    for x in hist.get("twin", []):
        s = stock(x["ticker"])
        if s and s["signal"] in COVER:
            hit(x["ticker"], x["name"], "up", "연기금·외국인 동시 매수 + 상환")

    # 대차 숫자를 붙이고, 상승 쪽·주의 쪽으로 나눠요.
    up, down = [], []
    for t, x in found.items():
        s = stock(t)
        if s:
            x["lend"] = {"signal": s["signal"], "chg5": s["chg5"], "chg20": s["chg20"],
                         "chg5_eok": s.get("chg5_eok"), "bal_eok": s["bal_eok"]}
        # 같은 종목이 양쪽 규칙에 다 걸리면(예: 연기금은 매수, 외국인은 매도) 더 많이 걸린 쪽에 둬요.
        if len(x["up"]) > len(x["down"]):
            x["rules"] = x["up"]; up.append(x)
        elif x["down"]:
            x["rules"] = x["down"]; down.append(x)
        del x["up"], x["down"]

    order = lambda x: (-len(x["rules"]), -abs(sum(x["week"].values()) if x["week"] else 0))
    out = {
        "version": 1,
        "date": hist["date"],
        "lending_date": lend["date"],
        "settings": {"vs_ratio": VS_RATIO},
        "up": sorted(up, key=order),
        "down": sorted(down, key=order),
    }
    with open("signals.json", "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"포착 완료: 상승 쪽 {len(up)}종목, 주의 {len(down)}종목")


if __name__ == "__main__":
    main()
