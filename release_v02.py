import time
import logging
import requests
from collections import defaultdict
from datetime import datetime, timedelta

# ================= LOGGER =================
logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s | %(levelname)-7s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger(__name__)

# ================= CONFIG =================
API_TOKEN = "247066-ZFfFhtCGjGEUhw"
LEAGUE_ID = '38439'
SPORT_ID = "1"
B365_API_BASE = "https://api.b365api.com/v3"

FORM_DAYS = 7
MIN_MATCHES = 4

MIN_HIGH_WR = 30.0
MAX_LOW_WR = 60.0
WINRATE_DIFF_THRESHOLD = 15.0

# Telegram
TELEGRAM_BOT_TOKEN = "8772522188:AAFUdQOlYiWoGfhfLLYNNDaHUL3dnFhU5Ck"
TELEGRAM_CHAT_ID = "5559311100"

# ================= GLOBAL STATE =================
alerted_match_ids = set()        # Lưu các trận đã gửi thông báo

def send_telegram(message):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "HTML"}
    try:
        r = requests.post(url, json=payload, timeout=10)
        if r.status_code != 200:
            logger.warning(f"Telegram gửi thất bại: {r.text}")
    except Exception as e:
        logger.error(f"Lỗi gửi Telegram: {e}")

# ================= HISTORY =================
player_history = defaultdict(lambda: {"win": 0, "draw": 0, "lose": 0, "matches": []})
processed_ids = set()

def extract_player(full):
    full = full.strip()
    if full.endswith("Esports"): full = full[:-7].strip()
    if full.endswith(") Esports"): full = full[:-9].strip()
    if '(' in full and ')' in full:
        start = full.rfind('(')
        end = full.rfind(')')
        if end > start:
            return full[start+1:end].strip()
    return full

def fetch_finished_matches(day=None, page=1):
    params = f"?token={API_TOKEN}&sport_id={SPORT_ID}&page={page}"
    if day: params += f"&day={day}"
    if LEAGUE_ID: params += f"&league_id={LEAGUE_ID}"
    
    url = f"{B365_API_BASE}/events/ended{params}"
    for attempt in range(3):
        try:
            resp = requests.get(url, timeout=30)
            resp.raise_for_status()
            data = resp.json()
            return data.get("results", []), data.get("pager", {}).get("total_pages", 1)
        except Exception as e:
            logger.warning(f"Fetch ended attempt {attempt+1} failed: {e}")
            time.sleep(5 * (attempt + 1))
    return [], 1

def process_match(m):
    eid = m.get('id')
    if not eid or eid in processed_ids or str(m.get('time_status')) != '3':
        return False
    ss = m.get("ss")
    if not ss: return False
    try:
        hg, ag = map(int, ss.split("-"))
    except:
        return False

    home = extract_player(m["home"]["name"])
    away = extract_player(m["away"]["name"])

    if hg > ag:
        player_history[home]["win"] += 1
        player_history[away]["lose"] += 1
    elif ag > hg:
        player_history[away]["win"] += 1
        player_history[home]["lose"] += 1
    else:
        player_history[home]["draw"] += 1
        player_history[away]["draw"] += 1

    player_history[home]["matches"].append({"home": True,  "hg": hg, "ag": ag})
    player_history[away]["matches"].append({"home": False, "hg": hg, "ag": ag})
    processed_ids.add(eid)
    return True

def initialize_historical_data():
    logger.info(f"===== KHỞI TẠO DỮ LIỆU {FORM_DAYS} NGÀY =====")
    end_date = datetime.now()
    start_date = end_date - timedelta(days=FORM_DAYS)

    for i in range(FORM_DAYS + 1):
        current = start_date + timedelta(days=i)
        day_str = current.strftime("%Y%m%d")
        logger.info(f"Xử lý ngày: {day_str}")

        page = 1
        total_pages = 1
        while page <= total_pages:
            matches, total_pages = fetch_finished_matches(day=day_str, page=page)
            count = sum(1 for m in matches if process_match(m))
            logger.debug(f"  Trang {page}/{total_pages} → xử lý {count} trận")
            page += 1
            time.sleep(1.2)

    logger.info("===== KHỞI TẠO HOÀN TẤT =====")
    logger.info(f"Tổng trận đã xử lý: {len(processed_ids)}")

    # Log winrate
    logger.info("=== WINRATE TOÀN BỘ PLAYER ===")
    sorted_players = sorted(player_history.items(), key=lambda x: len(x[1]["matches"]), reverse=True)
    for player, data in sorted_players[:30]:
        total = data["win"] + data["draw"] + data["lose"]
        wr = round(data["win"] / total * 100, 1) if total > 0 else 0.0
        last = get_last_result(player)
        logger.info(f"  {player:25} | {total:3} trận | WR={wr:5.1f}% | Last={last}")

def update_player_history():
    logger.debug("Cập nhật lịch sử mới nhất")
    new_count = sum(1 for m in fetch_finished_matches(page=1)[0] if process_match(m))
    if new_count > 0:
        logger.info(f"Đã cập nhật thêm {new_count} trận mới")

def get_winrate(player):
    if player not in player_history: return 0.0
    s = player_history[player]
    total = s["win"] + s["draw"] + s["lose"]
    if total < MIN_MATCHES or total == 0: return 0.0
    return round(s["win"] / total * 100, 1)

def get_last_result(player):
    matches = player_history[player]["matches"]
    if not matches: return None
    last = matches[-1]
    hg, ag = last["hg"], last["ag"]
    is_home = last["home"]
    if hg == ag: return "D"
    if is_home:
        return "W" if hg > ag else "L"
    return "W" if ag > hg else "L"

# ================= INPLAY =================
def fetch_inplay_matches():
    url = f"{B365_API_BASE}/events/inplay?sport_id={SPORT_ID}&league_id={LEAGUE_ID}&token={API_TOKEN}"
    logger.debug(f"Fetching inplay: {url}")
    try:
        r = requests.get(url, timeout=10).json()
        matches = r.get("results", [])
        logger.debug(f"Inplay API trả về {len(matches)} trận")
        return matches
    except Exception as e:
        logger.error(f"Inplay fetch error: {e}")
        return []

def check_and_alert():
    matches = fetch_inplay_matches()
    if not matches:
        logger.debug("Không có trận inplay nào")
        return

    logger.info(f"Inplay API trả về {len(matches)} trận - Bắt đầu kiểm tra...")

    for m in matches:
        mid = str(m.get("id"))
        if not mid:
            continue

        # Nếu trận này đã gửi alert rồi thì bỏ qua
        if mid in alerted_match_ids:
            logger.debug(f"Trận {mid} đã gửi alert trước đó → bỏ qua")
            continue

        ss = m.get("ss", "").strip()

        # === KHÔNG SKIP 0-0 nữa ===
        if not ss or ss == "*":
            logger.debug(f"Skip trận {mid}: chưa có dữ liệu tỷ số (ss='{ss}')")
            continue

        home = extract_player(m["home"]["name"])
        away = extract_player(m["away"]["name"])

        wr_h = get_winrate(home)
        wr_a = get_winrate(away)
        diff = abs(wr_h - wr_a)

        logger.info(f"→ Đang kiểm tra inplay: {home} vs {away} | Score: {ss} | WR: {wr_h}% - {wr_a}%")

        if diff <= WINRATE_DIFF_THRESHOLD:
            logger.info(f"   → Skip: Chênh lệch winrate quá nhỏ ({diff:.1f}% <= {WINRATE_DIFF_THRESHOLD}%)")
            continue

        if wr_h > wr_a:
            high, low = home, away
            high_wr, low_wr = wr_h, wr_a
        else:
            high, low = away, home
            high_wr, low_wr = wr_a, wr_h

        if high_wr <= MIN_HIGH_WR:
            logger.info(f"   → Skip: Winrate cao nhất quá thấp ({high_wr}% <= {MIN_HIGH_WR}%)")
            continue

        if low_wr >= MAX_LOW_WR:
            logger.info(f"   → Skip: Winrate thấp nhất quá cao ({low_wr}% >= {MAX_LOW_WR}%)")
            continue

        if get_last_result(high) != "W":
            logger.info(f"   → Skip: Trận gần nhất của {high} không phải thắng")
            continue

        # ========== THỎA MÃN → GỬI ALERT (CHỈ 1 LẦN) ==========
        logger.info(f"✅ THỎA MÃN INPLAY → {high} ({high_wr:.1f}%) vs {low} ({low_wr:.1f}%) | Score: {ss}")

        message = f"""
⚽ <b>{home}</b> vs {away}
📊 Winrate: <b>{wr_h:.1f}%</b> vs {wr_a:.1f}%
        """.strip()

        send_telegram(message)

        # Đánh dấu trận này đã gửi alert
        alerted_match_ids.add(mid)

# ================= MAIN =================
if __name__ == "__main__":
    initialize_historical_data()
    logger.info("🤖 Bot Inplay E-Soccer Volta đã khởi động - Mỗi trận chỉ gửi 1 lần")

    while True:
        try:
            update_player_history()
            check_and_alert()
            time.sleep(20)        # Kiểm tra mỗi 20 giây
        except Exception as e:
            logger.error(f"Lỗi vòng lặp: {e}")
            time.sleep(20)
