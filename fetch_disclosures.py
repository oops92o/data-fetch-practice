"""
금융감독원 DART에서 최근 며칠간의 상장사 공시 목록을 받아,
제목에 든 단어로 '호재성 / 악재성 / 실적'을 나눠 disclosures.json 으로 저장합니다.

준비물: DART Open API 인증키를 저장소 Secrets 에 DART_API_KEY 로 등록
        (추가 라이브러리 설치는 필요 없습니다)

주의: 분류는 '제목'만 보고 하는 단순 규칙입니다. 계약 금액이나 실적 내용은 보지 않으므로
      '호재성'은 "보통 좋게 읽히는 종류의 공시"라는 뜻이지, 실제로 좋다는 판정이 아닙니다.
"""

import json
import os
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

# ── 내 입맛대로 바꾸는 설정 ─────────────────────────────────────────
LOOKBACK_DAYS = 4      # 오늘부터 며칠 전까지 볼지 (주말을 끼워도 2~3거래일이 잡히게)
MAX_ITEMS = 200        # 파일에 담을 최대 건수

# 제목에 이 단어가 들어 있으면 해당 분류로 넣습니다. 위에서부터 차례로 확인하고
# 먼저 걸린 쪽으로 정해지므로, '해지'처럼 뜻을 뒤집는 단어는 악재를 먼저 봅니다.
# (띄어쓰기와 가운뎃점은 무시하고 비교하니 붙여서 적으세요)
RULES = [
    ("bad", [
        "단일판매공급계약해지", "유상증자결정", "전환사채권발행결정",
        "신주인수권부사채권발행결정", "교환사채권발행결정", "감자결정",
        "관리종목지정", "상장폐지", "불성실공시법인지정", "횡령", "배임",
        "매매거래정지", "영업정지", "회생절차", "부도발생", "소송등의제기",
    ]),
    ("earn", [
        "영업(잠정)실적", "매출액또는손익구조", "영업실적등에대한전망",
    ]),
    ("good", [
        "단일판매공급계약체결", "신규시설투자", "자기주식취득결정",
        "자기주식취득신탁계약체결", "주식소각결정", "무상증자결정",
        "현금현물배당결정", "기술이전", "품목허가", "임상시험계획승인",
    ]),
]

# 여기 적은 종목코드는 키워드에 안 걸려도 항상 담습니다. 예: ["005930", "000660"]
WATCHLIST = []

API_URL = "https://opendart.fss.or.kr/api/list.json"
VIEW_URL = "https://dart.fss.or.kr/dsaf001/main.do?rcpNo="
MARKETS = {"Y": "코스피", "K": "코스닥"}
KST = timezone(timedelta(hours=9))


# ── 1. DART에서 가져오기 ────────────────────────────────────────────
def call_dart(params):
    """DART 목록 API를 한 번 부르고, 받은 JSON을 그대로 돌려줍니다."""
    url = API_URL + "?" + urllib.parse.urlencode(params)
    with urllib.request.urlopen(url, timeout=30) as res:
        return json.loads(res.read().decode("utf-8"))


def fetch_market(api_key, market, bgn, end):
    """한 시장(코스피 또는 코스닥)의 공시를 여러 쪽에 걸쳐 모두 받습니다."""
    rows, page = [], 1
    while True:
        data = call_dart({
            "crtfc_key": api_key, "corp_cls": market,
            "bgn_de": bgn, "end_de": end,
            "page_no": page, "page_count": 100,
        })
        status = data.get("status")
        if status == "013":          # 013 = 조회된 데이터 없음 (오류가 아님)
            break
        if status != "000":          # 000 = 정상
            raise RuntimeError(f"DART 오류 {status}: {data.get('message')}")
        rows.extend(data.get("list", []))
        if page >= int(data.get("total_page", 1)) or page >= 40:
            break
        page += 1
        time.sleep(0.3)              # DART 서버에 부담을 주지 않게 잠깐 쉬기
    return rows


# ── 2. 분류 (인터넷 없이도 돌아가는 순수 계산) ──────────────────────
def squeeze(text):
    """비교하기 쉽게 띄어쓰기와 가운뎃점을 지웁니다."""
    for ch in (" ", "ㆍ", "·", "・", "\u3000"):
        text = text.replace(ch, "")
    return text


def classify(title):
    """제목을 보고 (분류, 걸린 단어)를 돌려줍니다. 안 걸리면 (None, None)."""
    flat = squeeze(title)
    for tag, words in RULES:
        for w in words:
            if squeeze(w) in flat:
                return tag, w
    return None, None


def pick(rows):
    """받은 공시 중 분류에 걸리거나 관심 종목인 것만 골라 정리합니다."""
    items, seen = [], set()
    for r in rows:
        rcept_no = r.get("rcept_no", "")
        if not rcept_no or rcept_no in seen:
            continue
        title = (r.get("report_nm") or "").strip()
        code = (r.get("stock_code") or "").strip()
        tag, word = classify(title)
        if tag is None:
            if code not in WATCHLIST:
                continue
            tag = "watch"
        seen.add(rcept_no)
        items.append({
            "no": rcept_no,
            "date": r.get("rcept_dt", ""),
            "corp": r.get("corp_name", ""),
            "code": code,
            "market": MARKETS.get(r.get("corp_cls", ""), ""),
            "title": title,
            "tag": tag,
            "word": word,
            "fix": "정정" in title.split("]")[0] if title.startswith("[") else False,
            "url": VIEW_URL + rcept_no,
        })
    # 접수번호는 날짜+순번이라, 큰 것이 더 최근 공시입니다.
    items.sort(key=lambda x: x["no"], reverse=True)
    return items[:MAX_ITEMS]


def build(rows, bgn, end, now):
    items = pick(rows)
    counts = {}
    for it in items:
        counts[it["tag"]] = counts.get(it["tag"], 0) + 1
    return {
        "updated": now.strftime("%Y-%m-%d %H:%M"),
        "from": bgn, "to": end,
        "scanned": len(rows),
        "counts": counts,
        "items": items,
    }


# ── 3. 실행 ─────────────────────────────────────────────────────────
if __name__ == "__main__":
    api_key = os.getenv("DART_API_KEY")
    if not api_key:
        raise RuntimeError("DART_API_KEY 가 없습니다. 저장소 Secrets 에 등록했는지 확인하세요.")

    now = datetime.now(KST)
    end = now.strftime("%Y%m%d")
    bgn = (now - timedelta(days=LOOKBACK_DAYS)).strftime("%Y%m%d")

    rows = []
    for market, label in MARKETS.items():
        got = fetch_market(api_key, market, bgn, end)
        print(label, len(got), "건 받음")
        rows.extend(got)

    result = build(rows, bgn, end, now)
    with open("disclosures.json", "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=1)
    print("저장 완료:", len(result["items"]), "건 /", result["counts"])
