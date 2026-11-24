"""
코스피200 선물의 투자자별 순매수와, 코스피 현물의 투자자별 순매수를 날짜마다 모아서
futures.json 으로 저장합니다. 대시보드 맨 위 '외국인 현물·선물' 카드가 이 파일을 씁니다.

원자료: 한국거래소 정보데이터시스템
  - 선물: [파생상품 > 거래실적 > 투자자별 거래실적] 화면 (MDCSTAT13101, 상품 KR___FUK2I = 코스피200 선물)
          정규장과 야간장을 합친 숫자예요(화면의 '전체'와 같아요).
  - 현물: pykrx 의 코스피 시장 투자자별 거래실적
  - 로그인: 저장소 Secrets 의 KRX_ID / KRX_PW (다른 스크립트와 같아요)

── 이렇게 읽어요 ─────────────────────────────────────────────────
선물 매수는 '지수가 오른다'에 거는 것, 현물 매수는 실제로 주식을 사는 것이에요.
  현물 매수 + 선물 매수 → 상승 쪽 확신이 큼
  현물 매도 + 선물 매수 → 반등 베팅 / 헤지 풀기 / 차익거래 중 하나 (다음 날 현물이 따라오는지 확인)
  현물 매수 + 선물 매도 → 종목은 사면서 지수 하락은 막아 둠(헤지)
  현물 매도 + 선물 매도 → 하락 쪽 확신이 큼
야간장 숫자는 다음 날 아침에 확정되므로, 매번 최근 며칠을 다시 받아 덮어써요.
"""

import json
import time
from datetime import datetime, timedelta

# ── 내 입맛대로 바꾸는 설정 ─────────────────────────────────────────
DAYS = 10             # 몇 거래일치를 보여 줄지
PRODUCT = "KR___FUK2I"  # 코스피200 선물
FUT_FLAT = 500        # 선물 순매수가 ±이 금액(억) 안이면 '중립'으로 봐요
SPOT_FLAT = 300       # 현물 순매수가 ±이 금액(억) 안이면 '중립'으로 봐요
EOK = 1e8


# ── 1. 받아 오기 ───────────────────────────────────────────────────
def futures_by_investor(day):
    """하루치 코스피200 선물 투자자별 순매수(억). 예) {"외국인": 1146.4, "개인": 81.8, ...}"""
    from pykrx.website.krx.krxio import KrxWebIo

    class FuturesInvestor(KrxWebIo):
        @property
        def bld(self):
            return "dbms/MDC/STAT/standard/MDCSTAT13101"

        def fetch(self, **params):
            return self.read(**params)

    raw = FuturesInvestor().fetch(
        locale="ko_KR", prodId="", isuCd=PRODUCT,
        strtDd=day, endDd=day, inqTpCd="1",     # 1 = 기간 합계 (시작일=종료일이라 그날 하루치)
        prtType="AMT", prtCheck="SUN", aggBasTpCd="",  # aggBasTpCd 비움 = 정규+야간 전체
        share="1", money="3", csvxls_isNo="false",
    )
    out = {}
    for row in raw.get("output", []):
        name = row.get("INVST_TP_NM", "").strip()
        val = float(str(row.get("NETBID_TRDVAL", "0")).replace(",", "") or 0)
        qty = float(str(row.get("NETBID_TRDVOL", "0")).replace(",", "") or 0)
        total = float(str(row.get("ACC_BID_TRDVAL", "0")).replace(",", "") or 0)
        out[name] = {"eok": round(val / EOK, 1), "qty": int(qty), "total": total}
    return out


def spot_by_day(days):
    """날짜별 코스피 현물 순매수(억): {"20261120": {"외국인": -2471.0, "기관": ..., "개인": ...}}"""
    from pykrx import stock

    df = stock.get_market_trading_value_by_date(days[0], days[-1], "KOSPI")
    out = {}
    for idx, row in df.iterrows():
        d = idx.strftime("%Y%m%d")
        out[d] = {
            "외국인": round(row.get("외국인합계", 0) / EOK, 1),
            "기관": round(row.get("기관합계", 0) / EOK, 1),
            "개인": round(row.get("개인", 0) / EOK, 1),
        }
    return out


def last_closed_day():
    """수급 확정치가 나오는 저녁 6시(한국시간) 전이면 오늘은 '아직 진행 중'으로 보고 빼요.
    GitHub 예약 실행이 늦어져 장중에 돌면, 반쪽짜리 오늘 숫자가 들어가는 걸 막으려는 거예요."""
    from datetime import timezone
    now = datetime.now(timezone(timedelta(hours=9)))
    if now.hour < 18:
        now -= timedelta(days=1)
    return now.strftime("%Y%m%d")


def vkospi_series(end_day, days=370):
    """코스피200 변동성지수(VKOSPI) 날짜별 종가: [("20261120", 53.18), ...] 오래된 것부터.
    한국거래소 [지수 > 파생 및 기타지수 > 개별지수 시세 추이] 화면과 같은 자료예요."""
    from pykrx.website.krx.krxio import KrxWebIo

    class IndexTrend(KrxWebIo):
        @property
        def bld(self):
            return "dbms/MDC/STAT/standard/MDCSTAT01201"

        def fetch(self, **params):
            return self.read(**params)

    start = (datetime.strptime(end_day, "%Y%m%d") - timedelta(days=days)).strftime("%Y%m%d")
    raw = IndexTrend().fetch(locale="ko_KR", indTpCd="1", idxIndCd="300", idxCd="1", idxCd2="300",
                             strtDd=start, endDd=end_day, csvxls_isNo="false")
    out = []
    for row in raw.get("output", []):
        d = str(row.get("TRD_DD", "")).replace("/", "")
        try:
            v = float(str(row.get("CLSPRC_IDX", "")).replace(",", ""))
        except ValueError:
            continue
        if d:
            out.append((d, v))
    if not out:
        print("  VKOSPI 응답 칸 이름을 확인하세요:", list((raw.get("output") or [{}])[0].keys()))
    return sorted(out)


def kospi_change(start, end):
    """날짜별 코스피 등락률(%)"""
    from pykrx import stock
    df = stock.get_index_ohlcv_by_date(start, end, "1001")
    closes = df["종가"].tolist()
    out = {}
    for i in range(1, len(closes)):
        out[df.index[i].strftime("%Y%m%d")] = round((closes[i] / closes[i - 1] - 1) * 100, 2)
    return out


VK_ZONES = [(20, "calm", "평상시"), (30, "normal", "다소 높음"), (50, "high", "높음"), (999, "extreme", "금융위기급")]


def vk_zone(v):
    for limit, code, label in VK_ZONES:
        if v < limit:
            return code, label
    return VK_ZONES[-1][1], VK_ZONES[-1][2]


def vk_note(spot5, vk5):
    """블로그의 '변동성 → 외국인 한도' 논리로 외국인 5일 현물과 VKOSPI 5일 변화를 겹쳐 읽어요."""
    if spot5 is None or vk5 is None:
        return None
    buy, calming = spot5 > SPOT_FLAT, vk5 < 0
    sell = spot5 < -SPOT_FLAT
    if buy and calming:
        return ("good", "변동성이 내려가며 외국인이 사는 중 → 한도가 열리는 매수라 이어질 가능성↑")
    if buy and not calming:
        return ("warn", "변동성이 오르는데 외국인이 사는 중 → 한도가 좁아지는 중이라 오래가기 어려울 수 있어요")
    if sell and not calming:
        return ("bad", "변동성↑ → 한도 축소 → 매도의 악순환 구간이에요")
    if sell and calming:
        return ("warn", "변동성은 진정 중인데 외국인은 아직 파는 중 → 한도 말고 다른 이유(업황 의심 등)를 의심해 볼 때예요")
    return ("", "외국인 현물이 중립이라 변동성과 겹쳐 읽기 어려워요")


def recent_trading_days(n):
    """코스피 현물 거래 기록에서 최근 n개 거래일을 뽑아요."""
    from pykrx import stock

    end = last_closed_day()
    start = (datetime.strptime(end, "%Y%m%d") - timedelta(days=n * 2 + 20)).strftime("%Y%m%d")
    df = stock.get_market_trading_value_by_date(start, end, "KOSPI")
    return [d.strftime("%Y%m%d") for d in df.index][-n:]


# ── 2. 해석 ────────────────────────────────────────────────────────
def side(v, flat):
    if v is None:
        return 0
    return 1 if v > flat else (-1 if v < -flat else 0)


QUAD = {
    (1, 1): ("both_buy", "현물·선물 동시 매수", "상승 쪽 확신이 커요"),
    (-1, 1): ("fut_only", "현물 매도 · 선물 매수", "반등 베팅·헤지 풀기·차익거래 중 하나예요. 다음 날 현물이 따라오는지 보세요"),
    (1, -1): ("hedge", "현물 매수 · 선물 매도", "종목은 사면서 지수 하락은 막아 두는 모양이에요"),
    (-1, -1): ("both_sell", "현물·선물 동시 매도", "하락 쪽 확신이 커요"),
}


def quad(spot, fut):
    key = (side(spot, SPOT_FLAT), side(fut, FUT_FLAT))
    if key in QUAD:
        return QUAD[key]
    return ("mixed", "뚜렷하지 않음", "한쪽이 중립 범위 안이라 방향을 말하기 어려워요")


def vkospi_block(rows):
    """VKOSPI 카드에 들어갈 숫자들. 실패해도 나머지 카드는 살아 있게 None 을 돌려줘요."""
    try:
        end = rows[-1]["date"]
        vk = [x for x in vkospi_series(end) if x[0] <= end]
        if len(vk) < 21:
            return None
        vals = [v for _, v in vk]
        now = vals[-1]
        zone, zone_label = vk_zone(now)
        kchg = kospi_change(rows[0]["date"], end)
        vk_by_day = dict(vk)
        for r in rows:                      # 선물 카드 표에도 날짜별 VKOSPI·코스피 등락을 붙여요
            r["vkospi"] = vk_by_day.get(r["date"])
            r["kospi_chg"] = kchg.get(r["date"])
        last_k = kchg.get(end)
        daily = round(now / 15.8, 2)        # 하루 예상 폭(1 표준편차) = VKOSPI ÷ √250
        spot5 = round(sum(r["spot_foreign"] or 0 for r in rows[-5:]), 1)
        vk5 = round(now - vals[-6], 2)
        note = vk_note(spot5, vk5)
        return {
            "date": vk[-1][0], "value": now, "zone": zone, "zone_label": zone_label,
            "chg1": round(now - vals[-2], 2), "chg5": vk5, "chg20": round(now - vals[-21], 2),
            "high52": max(vals[-250:]), "low52": min(vals[-250:]),
            "pct_rank": round(sum(v <= now for v in vals[-250:]) / len(vals[-250:]) * 100),
            "daily_range": daily, "month_range": round(now / 3.46, 1),
            "kospi_chg": last_k,
            "kospi_in_range": (abs(last_k) <= daily) if last_k is not None else None,
            # 변동성 타깃팅: 같은 손실 한도라면 투자 한도는 변동성에 반비례해요
            "cap_vs20": round(20 / now, 2), "cap_if30": round(now / 30, 2),
            "note": note,
            "series": [{"date": d, "v": v} for d, v in vk[-60:]],
        }
    except Exception as e:
        print("  VKOSPI 수집 실패(나머지는 그대로 저장):", e)
        return None


def main():
    days = recent_trading_days(DAYS + 1)   # 전날 비교용으로 하루 더
    print("거래일:", days[0], "~", days[-1])
    spot = spot_by_day(days)

    rows = []
    for d in days:
        f = futures_by_investor(d)
        if not f or f.get("합계", {}).get("total", 0) == 0:
            print("  선물 자료 없음, 건너뜀:", d)
            continue
        fo = f.get("외국인", {})
        s = spot.get(d, {})
        code, label, _ = quad(s.get("외국인"), fo.get("eok"))
        rows.append({
            "date": d,
            "fut_foreign": fo.get("eok"), "fut_foreign_qty": fo.get("qty"),
            "fut_inst": f.get("기관합계", {}).get("eok"), "fut_indiv": f.get("개인", {}).get("eok"),
            "spot_foreign": s.get("외국인"), "spot_inst": s.get("기관"), "spot_indiv": s.get("개인"),
            "quad": code, "label": label,
        })
        print(f"  {d}: 선물 외국인 {fo.get('eok')}억 / 현물 외국인 {s.get('외국인')}억 → {label}")
        time.sleep(0.5)

    if not rows:
        raise RuntimeError("선물 자료를 하나도 받지 못했어요.")

    last = rows[-1]
    prev = rows[-2] if len(rows) >= 2 else None
    _, label, meaning = quad(last["spot_foreign"], last["fut_foreign"])

    # 외국인 선물이 같은 방향으로 며칠째인지
    streak, sign = 0, side(last["fut_foreign"], 0)
    for r in reversed(rows):
        if sign != 0 and side(r["fut_foreign"], 0) == sign:
            streak += 1
        else:
            break

    # 어제 선물로 반등에 걸었다면, 오늘 현물이 따라왔는지
    follow = None
    if prev and side(prev["fut_foreign"], FUT_FLAT) == 1 and side(prev["spot_foreign"], SPOT_FLAT) <= 0:
        s = side(last["spot_foreign"], SPOT_FLAT)
        follow = {"prev_date": prev["date"],
                  "result": "followed" if s == 1 else ("not_yet" if s == 0 else "failed")}

    data = {
        "version": 1,
        "date": last["date"],
        "product": "코스피200 선물 (정규+야간)",
        "settings": {"fut_flat": FUT_FLAT, "spot_flat": SPOT_FLAT},
        "summary": {
            "quad": last["quad"], "label": label, "meaning": meaning,
            "fut_streak": streak * (1 if sign >= 0 else -1),
            "fut_5d": round(sum(r["fut_foreign"] or 0 for r in rows[-5:]), 1),
            "spot_5d": round(sum(r["spot_foreign"] or 0 for r in rows[-5:]), 1),
            "follow": follow,
        },
        "days": rows[-DAYS:],
        "vkospi": vkospi_block(rows),
    }
    with open("futures.json", "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    print("저장 완료:", data["date"], label)


if __name__ == "__main__":
    main()
