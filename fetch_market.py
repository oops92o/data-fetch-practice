"""
주요 지수(코스피, 코스닥, 코스피 200)와 코스피 업종별 등락률을 받아 market.json 으로 저장합니다.
업종마다 시가총액이 큰 구성 종목과 그 종목들의 등락률도 함께 담습니다 (대시보드에서 업종을 누르면 보여요).

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

MEMBERS_TOP = 15   # 업종마다 시가총액 상위 몇 종목까지 담을지

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


def fetch_members(days, sector_names):
    """업종별 구성 종목과 종목별 시세를 받습니다.

      members : {"전기전자": ["005930", "000660", ...], ...}
      quotes  : {"005930": {"d1": -1.2, "close": 70000.0, "close_w": 72000.0, "cap": 4.1e14}, ...}
      names   : {"005930": "삼성전자", ...}
    """
    from pykrx import stock

    week_ago, _, today = days
    wanted = {squeeze(n) for n in sector_names}

    members = {}
    for t in stock.get_index_ticker_list(today, market="KOSPI"):
        name = squeeze(stock.get_index_ticker_name(t))
        if name not in wanted:
            continue
        try:
            tickers = list(stock.get_index_portfolio_deposit_file(t, today))
        except Exception as e:       # 한 업종이 실패해도 나머지는 계속 받아요
            print("  구성 종목을 받지 못한 업종:", name, e)
            continue
        if tickers:
            members[name] = tickers
        time.sleep(0.5)
    print("  업종 구성 종목:", len(members), "개 업종")

    cap = stock.get_market_cap_by_ticker(today, market="KOSPI")
    now = stock.get_market_ohlcv_by_ticker(today, market="KOSPI")
    time.sleep(0.5)
    old = stock.get_market_ohlcv_by_ticker(week_ago, market="KOSPI")

    quotes = {}
    for t, r in now.iterrows():
        quotes[t] = {
            "d1": float(r["등락률"]),
            "close": float(r["종가"]),
            "close_w": float(old.loc[t, "종가"]) if t in old.index else 0.0,
            "cap": float(cap.loc[t, "시가총액"]) if t in cap.index else 0.0,
        }

    # 종목명은 화면에 나갈 종목만 찾아요
    needed = set()
    for tickers in members.values():
        top = sorted(tickers, key=lambda t: quotes.get(t, {}).get("cap", 0), reverse=True)
        needed.update(top[:MEMBERS_TOP])
    names = {}
    for t in needed:
        try:
            names[t] = stock.get_market_ticker_name(t)
        except Exception:
            names[t] = t
    return members, quotes, names


# ── 2. 계산 (인터넷 없이도 돌아가는 순수 계산) ──────────────────────
def squeeze(name):
    """'음식료·담배'와 '음식료담배'를 같은 이름으로 보도록 띄어쓰기와 가운뎃점을 지웁니다."""
    for ch in (" ", "·", "ㆍ", "・"):
        name = name.replace(ch, "")
    return name


def attach_members(result, members, quotes, names):
    """업종마다 시가총액 상위 종목과 상승·하락 종목 수를 붙입니다."""
    for sec in result["sectors"]:
        tickers = [t for t in members.get(squeeze(sec["name"]), []) if t in quotes]
        if not tickers:
            continue
        sec["up"] = sum(1 for t in tickers if quotes[t]["d1"] > 0)
        sec["down"] = sum(1 for t in tickers if quotes[t]["d1"] < 0)
        sec["count"] = len(tickers)

        top = sorted(tickers, key=lambda t: quotes[t]["cap"], reverse=True)[:MEMBERS_TOP]
        rows = []
        for t in top:
            q = quotes[t]
            row = {
                "ticker": t,
                "name": names.get(t, t),
                "cap_eok": round(q["cap"] / 1e8),
                "d1": round(q["d1"], 2),
            }
            if q["close"] > 0 and q["close_w"] > 0:
                row["w1"] = pct(q["close"], q["close_w"])
            rows.append(row)
        sec["members"] = rows
    return result

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

    # 구성 종목은 '있으면 좋은' 정보라, 실패해도 지수·업종 등락은 그대로 저장해요.
    try:
        members, quotes, names = fetch_members(days, [s["name"] for s in result["sectors"]])
        attach_members(result, members, quotes, names)
    except Exception as e:
        print("  구성 종목 단계를 건너뜁니다:", e)

    with open("market.json", "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=1)
    print("저장 완료:", result["date"], "/ 지수", len(result["indices"]), "개, 업종", len(result["sectors"]), "개")
    print("  업종:", ", ".join(s["name"] for s in result["sectors"]))
