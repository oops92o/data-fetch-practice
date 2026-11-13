"""
disclosures.json 에서 '아직 알리지 않은' 공시를 골라 텔레그램으로 보냅니다.
fetch_disclosures.py 가 끝난 뒤에 이어서 실행됩니다.

준비물: 저장소 Secrets 에 TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID 등록
        (둘 중 하나라도 없으면 아무것도 보내지 않고 조용히 끝납니다)

이미 보낸 공시의 접수번호는 notified.json 에 적어 두어, 같은 공시를 두 번 보내지 않습니다.
"""

import html
import json
import os
import urllib.parse
import urllib.request

# ── 내 입맛대로 바꾸는 설정 ─────────────────────────────────────────
# 이 단어로 분류된 공시만 알립니다. fetch_disclosures.py 의 RULES 에 적힌 글자 그대로 쓰세요.
# 비워 두면([]) 호재성·악재성·실적 전부를 알립니다 (하루 수백 건이 될 수 있어요).
ALERT_WORDS = ["단일판매공급계약체결", "신규시설투자"]
ALERT_WATCHLIST = True     # 관심 종목(WATCHLIST)의 공시는 단어와 상관없이 알릴지
SKIP_FIX = True            # [기재정정] 같은 정정 공시는 건너뛸지
PER_MESSAGE = 15           # 메시지 하나에 공시를 몇 건까지 묶을지
MAX_PER_RUN = 60           # 한 번 실행에 최대 몇 건까지 보낼지 (폭주 방지)

SENT_FILE = "notified.json"
TAG_MARK = {"good": "🔴 호재성", "bad": "🔵 악재성", "earn": "⚪ 실적", "watch": "⭐ 관심"}


# ── 1. 고르기 (인터넷 없이도 돌아가는 순수 계산) ────────────────────
def wanted(item):
    if SKIP_FIX and item.get("fix"):
        return False
    if item.get("tag") == "watch":
        return ALERT_WATCHLIST
    return not ALERT_WORDS or item.get("word") in ALERT_WORDS


def pick_new(items, sent):
    """알릴 대상 중 아직 안 보낸 것만, 오래된 것부터 차례로 돌려줍니다."""
    fresh = [x for x in items if wanted(x) and x["no"] not in sent]
    return sorted(fresh, key=lambda x: x["no"])[:MAX_PER_RUN]


def format_messages(items):
    """공시 여러 건을 텔레그램 메시지 몇 개로 묶습니다."""
    messages = []
    for i in range(0, len(items), PER_MESSAGE):
        lines = []
        for x in items[i:i + PER_MESSAGE]:
            mark = TAG_MARK.get(x["tag"], x["tag"])
            lines.append(
                f'{mark}  <b>{html.escape(x["corp"])}</b>\n'
                f'<a href="{x["url"]}">{html.escape(x["title"])}</a>'
            )
        messages.append("\n\n".join(lines))
    return messages


# ── 2. 보내기 ───────────────────────────────────────────────────────
def send(token, chat_id, text):
    data = urllib.parse.urlencode({
        "chat_id": chat_id, "text": text,
        "parse_mode": "HTML", "disable_web_page_preview": "true",
    }).encode("utf-8")
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    with urllib.request.urlopen(url, data=data, timeout=30) as res:
        answer = json.loads(res.read().decode("utf-8"))
    if not answer.get("ok"):
        raise RuntimeError(f"텔레그램 오류: {answer}")


def load_sent():
    if not os.path.exists(SENT_FILE):
        return None          # 파일이 없다 = 첫 실행
    with open(SENT_FILE, encoding="utf-8") as f:
        return set(json.load(f))


def save_sent(sent):
    with open(SENT_FILE, "w", encoding="utf-8") as f:
        json.dump(sorted(sent)[-3000:], f)     # 최근 3,000건만 기억


# ── 3. 실행 ─────────────────────────────────────────────────────────
def main():
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        print("텔레그램 Secrets 가 없어 알림을 건너뜁니다.")
        return

    with open("disclosures.json", encoding="utf-8") as f:
        items = json.load(f)["items"]

    sent = load_sent()
    if sent is None:
        # 첫 실행: 지금 있는 공시를 전부 '보낸 것'으로 치고, 시작 인사만 보냅니다.
        # (안 그러면 며칠 치 공시가 한꺼번에 쏟아져요)
        save_sent({x["no"] for x in items})
        send(token, chat_id, "공시 알림을 시작해요. 지금부터 새로 올라오는 공시를 알려 드릴게요.")
        print("첫 실행: 기준점만 저장했습니다.")
        return

    fresh = pick_new(items, sent)
    for text in format_messages(fresh):
        send(token, chat_id, text)
    # 알리지 않기로 한 공시도 '본 것'으로 적어 두어 목록이 계속 커지지 않게 합니다.
    save_sent(sent | {x["no"] for x in items})
    print("알림", len(fresh), "건 보냄")


if __name__ == "__main__":
    main()
