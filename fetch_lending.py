"""
코스피·코스닥 전 종목의 대차잔고(빌려 간 주식 중 아직 안 갚은 양)를 모아서 lending.json 으로 저장합니다.
GitHub Actions가 이 스크립트를 매일 대신 실행해줍니다.

원자료: 공공데이터포털 '금융위원회_주식대차거래정보' (getStckLnbDetail)
  - 준비물: pip install requests
  - 인증키: 저장소 Settings → Secrets 의 DATA_GO_KEY (공공데이터포털 '일반 인증키(Decoding)')
  - 이 자료는 기준일 다음 영업일 13시 이후에 올라와요. 그래서 연기금 데이터보다 하루 늦어요.

── 대차잔고를 이렇게 읽어요 ───────────────────────────────────────
공매도를 하려면 먼저 주식을 빌려야 해요. 그래서 대차잔고는 '공매도 대기 물량'을 짐작하는 대용 지표예요.
  · 잔고가 줄어든다 = 빌린 주식을 갚는 중 = 공매도 청산(숏커버) 쪽 → 주가에는 받쳐 주는 힘
  · 잔고가 늘어난다 = 새로 빌려 가는 중 = 공매도 준비 쪽 → 주가에는 누르는 힘
다만 빌린 주식이 전부 공매도에 쓰이지는 않아요(차익거래·ETF 헤지 등). 방향과 속도를 보는 용도예요.
"""

import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import unquote

import requests

# ── 내 입맛대로 바꾸는 설정 ─────────────────────────────────────────
URL = "https://apis.data.go.kr/1160100/service/GetCMStckLnbInfoService/getStckLnbDetail"
MARKETS = ["코스피", "코스닥"]
SHORT = 5             # 짧은 비교: 5거래일 전 잔고와 비교
LONG = 20             # 긴 비교: 20거래일 전 잔고와 비교
MIN_EOK = 10          # 잔고가 이보다 작으면(억) 신호를 붙이지 않아요 (작은 숫자는 % 가 크게 출렁여서)
MOVER_MIN_EOK = 300   # '급감·급증 상위' 목록에 올릴 최소 잔고(억)
TOP_N = 10
STRONG = 10.0         # 20일 변화가 ±10% 이상이고 5일도 같은 방향이면 '강한' 신호
SOFT = 5.0            # 5일 변화가 ±5% 이상이면 '최근' 신호
EOK = 1e8
KST = timezone(timedelta(hours=9))

# 일별 기록 (lending_history.json): 종목을 누르면 대차잔고와 주가를 겹쳐 보여 주는 데 써요.
HIST_FILE = "lending_history.json"
HIST_DAYS = 60        # 최근 몇 거래일을 쌓아 둘지
HIST_MIN_EOK = 100    # 기록 기간 중 잔고가 한 번이라도 이 금액(억) 이상이었던 종목만 기록해요 (파일 크기 때문)


# ── 1. API 부르기 ──────────────────────────────────────────────────
def service_key():
    key = os.environ.get("DATA_GO_KEY", "").strip()
    if not key:
        print("DATA_GO_KEY 가 비어 있어요. 저장소 Settings → Secrets 에 인증키를 넣어 주세요.")
        sys.exit(2)   # 2 = 다시 해도 소용없는 오류 (워크플로가 5분 기다리지 않고 바로 멈춰요)
    # 'Encoding' 키를 넣었어도 동작하게, % 가 들어 있으면 한 번 풀어 줘요.
    return unquote(key) if "%" in key else key


def call(day, market, rows, page=1):
    """하루·한 시장의 응답에서 (행 목록, 전체 건수)를 돌려줘요. 3번까지 다시 시도해요."""
    params = {
        "serviceKey": service_key(), "resultType": "json",
        "numOfRows": rows, "pageNo": page, "basDt": day, "mrktClsfNm": market,
    }
    last = None
    for attempt in range(3):
        try:
            res = requests.get(URL, params=params, timeout=60)
            text = res.text.strip()
            if not text.startswith("{"):
                # 인증키 문제면 JSON 대신 XML 오류가 와요.
                if "SERVICE_KEY" in text or "SERVICE KEY" in text:
                    print(
                        "인증키 오류예요. ① 활용신청이 승인됐는지 ② 'Decoding' 키를 넣었는지 확인해 주세요. "
                        "새로 받은 키는 1시간쯤 지나야 동작하기도 해요.\n응답: " + text[:300])
                    sys.exit(2)
                raise RuntimeError("JSON 이 아닌 응답: " + text[:300])
            body = parse(res.json())
            return body
        except SystemExit:
            raise
        except Exception as e:  # 네트워크 끊김·타임아웃 등
            last = e
            print(f"  다시 시도 ({attempt + 1}/3): {day} {market} - {e}")
            time.sleep(5 * (attempt + 1))
    raise RuntimeError(f"{day} {market} 를 받지 못했어요: {last}")


def parse(payload):
    resp = payload.get("response", {})
    header = resp.get("header", {})
    if header.get("resultCode") not in ("00", None):
        raise RuntimeError(f"API 오류 {header.get('resultCode')}: {header.get('resultMsg')}")
    body = resp.get("body", {})
    items = body.get("items") or {}
    item = items.get("item", []) if isinstance(items, dict) else []
    if isinstance(item, dict):
        item = [item]
    return item, int(body.get("totalCount") or 0)


def num(v):
    try:
        return float(str(v).replace(",", ""))
    except (TypeError, ValueError):
        return 0.0


def ticker_of(it):
    code = str(it.get("stckItmsCd") or "").strip()
    if len(code) == 12 and code.startswith("KR"):   # KR7005930003 같은 표준코드면 가운데 6자리
        code = code[3:9]
    return code.lstrip("A")


def fetch_day(day):
    """하루치 전 종목: {종목코드: {name, bal, bal_amt, lent, repaid}}"""
    out = {}
    for market in MARKETS:
        page = 1
        while True:
            items, total = call(day, market, 10000, page)
            for it in items:
                t = ticker_of(it)
                if not t:
                    continue
                out[t] = {
                    "name": (it.get("stckItmsNm") or it.get("itmsNm") or t).strip(),
                    "market": market,
                    "bal": num(it.get("balnStckCnt")),        # 잔고 주식 수
                    "bal_amt": num(it.get("balnStckAmt")),    # 잔고 금액(원) = 잔고 주식 수 × 종가
                    "lent": num(it.get("cclStckCnt")),        # 그날 새로 빌려 간 주식 수(체결)
                    "repaid": num(it.get("rdptStckCnt")),     # 그날 갚은 주식 수(상환)
                }
            if not items or page * 10000 >= total:
                break
            page += 1
    return out


# ── 2. 거래일 찾기 ─────────────────────────────────────────────────
def find_trading_days(n):
    """오늘부터 거꾸로, 자료가 있는 날을 n개 찾아요 (최신이 맨 앞).
    날마다 1건만 받아 보는 가벼운 호출이라 금방 끝나요."""
    days = []
    d = datetime.now(KST)
    for _ in range(n * 2 + 20):
        if d.weekday() < 5:   # 토·일은 부르지 않아요
            ds = d.strftime("%Y%m%d")
            _, total = call(ds, MARKETS[0], 1)
            if total > 0:
                days.append(ds)
                if len(days) == n:
                    return days
        d -= timedelta(days=1)
    raise RuntimeError(f"자료가 있는 거래일을 {n}개 찾지 못했어요 (찾은 것: {days})")


# ── 3. 해석 ────────────────────────────────────────────────────────
def pct(now, before):
    if before <= 0:
        return None
    return round((now / before - 1) * 100, 1)


def signal(bal_eok, c5, c20):
    """잔고 변화를 한 단어로 정리해요.
    cover  : 숏커버 진행 (20일 −10% 이상 줄었고 최근 5일도 감소)
    cover2 : 최근 상환 많음 (5일 −5% 이상)
    short  : 공매도 대기 증가 (20일 +10% 이상 늘었고 최근 5일도 증가)
    short2 : 최근 차입 많음 (5일 +5% 이상)
    flat   : 변화 작음
    small  : 잔고가 작아 의미 없음"""
    if bal_eok < MIN_EOK or c5 is None:
        return "small"
    if c20 is not None and c20 <= -STRONG and c5 < 0:
        return "cover"
    if c20 is not None and c20 >= STRONG and c5 > 0:
        return "short"
    if c5 <= -SOFT:
        return "cover2"
    if c5 >= SOFT:
        return "short2"
    return "flat"


def build(snap, days):
    """snap: {날짜: 하루치}, days: 최신부터 거래일 목록"""
    today = snap[days[0]]
    d5, d20 = snap[days[SHORT]], snap[days[LONG]]
    recent = [snap[d] for d in days[:SHORT]]

    stocks = {}
    for t, x in today.items():
        bal_eok = round(x["bal_amt"] / EOK, 1)
        c5 = pct(x["bal"], d5[t]["bal"]) if t in d5 else None
        c20 = pct(x["bal"], d20[t]["bal"]) if t in d20 else None
        lent5 = sum(r[t]["lent"] for r in recent if t in r)
        repaid5 = sum(r[t]["repaid"] for r in recent if t in r)
        # 잔고 주식 수가 늘거나 준 만큼을 오늘 종가로 환산한 금액(억). 순매수 금액과 나란히 비교하려고요.
        price = x["bal_amt"] / x["bal"] if x["bal"] > 0 else 0
        d5_eok = round((x["bal"] - d5[t]["bal"]) * price / EOK, 1) if t in d5 else None
        d20_eok = round((x["bal"] - d20[t]["bal"]) * price / EOK, 1) if t in d20 else None
        stocks[t] = [x["name"], bal_eok, c5, c20, signal(bal_eok, c5, c20),
                     round(repaid5 / lent5, 2) if lent5 > 0 else None, d5_eok, d20_eok]

    # 시장 전체 잔고 금액 합계
    def total(day_snap, market):
        return sum(v["bal_amt"] for v in day_snap.values() if v["market"] == market)

    market = []
    for m in MARKETS:
        now, b5, b20 = total(today, m), total(d5, m), total(d20, m)
        market.append({"name": m, "bal_jo": round(now / 1e12, 2),
                       "chg5": pct(now, b5), "chg20": pct(now, b20)})

    big = [(t, v) for t, v in stocks.items() if v[1] >= MOVER_MIN_EOK and v[3] is not None]
    def pack(rows):
        return [{"ticker": t, "name": v[0], "bal_eok": v[1], "chg5": v[2], "chg20": v[3]} for t, v in rows]
    falling = pack(sorted(big, key=lambda kv: kv[1][3])[:TOP_N])
    rising = pack(sorted(big, key=lambda kv: -kv[1][3])[:TOP_N])

    return {
        "version": 1,
        "date": days[0],
        "compare": {"short": days[SHORT], "long": days[LONG]},
        "settings": {"min_eok": MIN_EOK, "mover_min_eok": MOVER_MIN_EOK, "strong": STRONG, "soft": SOFT},
        "market": market,
        "falling": [x for x in falling if x["chg20"] < 0],
        "rising": [x for x in rising if x["chg20"] > 0],
        # 종목마다 [이름, 잔고(억), 5일 변화%, 20일 변화%, 신호, 최근 5일 상환÷체결, 5일 변화(억), 20일 변화(억)]
        "fields": ["name", "bal_eok", "chg5", "chg20", "signal", "repay_ratio5", "chg5_eok", "chg20_eok"],
        "stocks": stocks,
    }


# ── 4. 일별 기록 쌓기 ──────────────────────────────────────────────
def load_history():
    """지난번에 저장한 기록을 {날짜: {종목코드: (잔고 주식 수, 종가)}} 모양으로 바꿔 읽어요."""
    if not os.path.exists(HIST_FILE):
        return {}, {}
    try:
        with open(HIST_FILE, encoding="utf-8") as f:
            h = json.load(f)
    except (ValueError, OSError):
        return {}, {}
    cols = {d: {} for d in h.get("dates", [])}
    for t, bals in h.get("bal", {}).items():
        pxs = h.get("px", {}).get(t, [])
        for i, d in enumerate(h.get("dates", [])):
            b = bals[i] if i < len(bals) else None
            p = pxs[i] if i < len(pxs) else None
            if b is not None and p is not None:
                cols[d][t] = (b, p)
    return cols, h.get("names", {})


def snap_to_col(day_snap):
    """하루치 원자료를 {종목코드: (잔고 주식 수, 종가)} 로 줄여요. 종가 = 잔고 금액 ÷ 잔고 주식 수."""
    col = {}
    for t, x in day_snap.items():
        if x["bal"] > 0 and x["bal_amt"] > 0:
            col[t] = (int(round(x["bal"])), int(round(x["bal_amt"] / x["bal"])))
    return col


def build_history(cols, names):
    """날짜별 자료를 종목별 줄 맞춤 배열로 바꿔요. 없는 날은 null 이에요."""
    dates = sorted(cols)[-HIST_DAYS:]
    keep = set()
    for d in dates:
        for t, (b, p) in cols[d].items():
            if b * p >= HIST_MIN_EOK * EOK:
                keep.add(t)
    bal, px = {}, {}
    for t in sorted(keep):
        bal[t] = [cols[d][t][0] if t in cols[d] else None for d in dates]
        px[t] = [cols[d][t][1] if t in cols[d] else None for d in dates]
    return {
        "version": 1,
        "dates": dates,
        "min_eok": HIST_MIN_EOK,
        "names": {t: names.get(t, t) for t in sorted(keep)},
        "bal": bal,   # 대차잔고 주식 수
        "px": px,     # 그날 종가 (원)
    }


def update_history(snap, days):
    """이미 받은 날은 다시 받지 않고, 비어 있는 날만 새로 받아 기록을 채워요.
    처음 한 번은 60일을 다 받아야 해서 몇 분 더 걸려요."""
    cols, names = load_history()
    for d, day_snap in snap.items():          # 이번에 이미 받은 날은 그대로 써요
        cols[d] = snap_to_col(day_snap)
        names.update({t: x["name"] for t, x in day_snap.items()})

    wanted = sorted(find_trading_days(HIST_DAYS))
    missing = [d for d in wanted if d not in cols]
    if missing:
        print(f"  일별 기록: 비어 있는 {len(missing)}일을 받아요 ({missing[0]} ~ {missing[-1]})")
    for d in missing:
        day_snap = fetch_day(d)
        cols[d] = snap_to_col(day_snap)
        names.update({t: x["name"] for t, x in day_snap.items()})
        print(f"    {d}: {len(cols[d])}종목")

    cols = {d: cols[d] for d in wanted if d in cols}
    hist = build_history(cols, names)
    with open(HIST_FILE, "w", encoding="utf-8") as f:
        json.dump(hist, f, ensure_ascii=False, separators=(",", ":"))
    print(f"일별 기록 저장: {len(hist['dates'])}거래일, {len(hist['bal'])}종목")


def main():
    print("자료가 있는 거래일 찾는 중...")
    days = find_trading_days(LONG + 1)
    print("  최신:", days[0], "/ 5일 전:", days[SHORT], "/ 20일 전:", days[LONG])

    need = sorted(set(days[:SHORT + 1] + [days[LONG]]))
    snap = {}
    for d in need:
        snap[d] = fetch_day(d)
        print(f"  {d}: {len(snap[d])}종목")
    if not snap[days[0]]:
        raise RuntimeError("최신 거래일 자료가 비어 있어요.")

    data = build(snap, days)
    with open("lending.json", "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, separators=(",", ":"))
    print("저장 완료:", data["date"], f"({len(data['stocks'])}종목)")

    # 일별 기록은 '있으면 좋은' 자료라, 실패해도 위 lending.json 은 그대로 둬요.
    try:
        update_history(snap, days)
    except SystemExit:
        raise
    except Exception as e:
        print("일별 기록 단계를 건너뜁니다:", e)


if __name__ == "__main__":
    main()
