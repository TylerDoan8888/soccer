import time
import logging
import requests
from collections import defaultdict
from datetime import datetime, timedelta

# ================= LOGGER =================
logging.basicConfig(
    level=logging.INFO,
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

# Thời gian gửi alert trước khi trận bắt đầu (giây)
ALERT_BEFORE_SECONDS = 30

def send_telegram(message):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "HTML"
    }
    try:
        r = requests.post(url, json=payload, timeout=10)
        if r.status_code != 200:
            logger.warning(f"Telegram gửi thất bại: {r.text}")
    except Exception as e:
        logger.error(f"Lỗi gửi Telegram: {e}")

# ================= HISTORY =================
player_history = defaultdict(lambda: {
    "win": 0, "draw": 0, "lose": 0, "matches": []
})
processed_ids = set()

def extract_player(full):
    full = full.strip()
    if full.endswith("Esports"):
        full = full[:-7].strip()
    if full.endswith(") Esports"):
        full = full[:-9].strip()

    if '(' in full and ')' in full:
        start = full.rfind('(')
        end = full.rfind(')')
        if start != -1 and end != -1 and end > start:
            return full[start+1:end].strip()
    return full

def fetch_finished_matches(day=None, page=1):
    params = f"?token={API_TOKEN}&sport_id={SPORT_ID}&page={page}"
    if day:
        params += f"&day={day}"
    if LEAGUE_ID:
        params += f"&league_id={LEAGUE_ID}"

    url = f"{B365_API_BASE}/events/ended{params}"
    
    for attempt in range(3):
        try:
            resp = requests.get(url, timeout=30)
            resp.raise_for_status()
            data = resp.json()
            return data.get("results", []), data.get("pager", {}).get("total_pages", 1)
        except Exception as e:
            logger.warning(f"Fetch ended attempt {attempt+1} thất bại: {e}")
            time.sleep(5 * (attempt + 1))
    return [], 1

def process_match(m):
    eid = m.get('id')
    if not eid or eid in processed_ids:
        return False
    if str(m.get('time_status')) != '3':
        return False

    ss = m.get("ss")
    if not ss:
        return False
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
            for m in matches:
                process_match(m)
            page += 1
            time.sleep(1.2)

    logger.info("===== KHỞI TẠO HOÀN TẤT =====")

def update_player_history():
    logger.debug("Đang cập nhật lịch sử mới nhất...")
    new_count = 0
    matches, _ = fetch_finished_matches(page=1)
    for m in matches:
        if process_match(m):
            new_count += 1
    if new_count > 0:
        logger.info(f"Đã cập nhật thêm {new_count} trận mới")

def get_winrate(player):
    if player not in player_history:
        return 0.0
    s = player_history[player]
    total = s["win"] + s["draw"] + s["lose"]
    if total < MIN_MATCHES or total == 0:
        return 0.0
    return round(s["win"] / total * 100, 1)

def get_last_result(player):
    matches = player_history[player]["matches"]
    if not matches:
        return None
    last = matches[-1]
    hg, ag = last["hg"], last["ag"]
    is_home = last["home"]
    if hg == ag:
        return "D"
    if is_home:
        return "W" if hg > ag else "L"
    return "W" if ag > hg else "L"

# ================= UPCOMING =================
def fetch_upcoming_matches():
    url = f"{B365_API_BASE}/events/upcoming?sport_id={SPORT_ID}&league_id={LEAGUE_ID}&token={API_TOKEN}"
    try:
        r = requests.get(url, timeout=10).json()
        return r.get("results", [])
    except Exception as e:
        logger.error(f"Upcoming fetch error: {e}")
        return []

def check_and_alert():
    now_unix = int(time.time())
    matches = fetch_upcoming_matches()
    if not matches:
        return

    for m in matches:
        mid = m.get("id")
        if not mid:
            continue

        # Lấy thời gian bắt đầu (unix timestamp - UTC)
        start_time = int(m.get("time", 0))
        if start_time == 0:
            continue

        seconds_to_start = start_time - now_unix

        # Chỉ xử lý các trận sắp bắt đầu trong khoảng 1 - 10 phút tới
        if not (30 <= seconds_to_start <= 600):
            continue

        # Gửi alert khi còn đúng ~30 giây
        if seconds_to_start > ALERT_BEFORE_SECONDS + 8:   # cho phép lệch ±8s
            continue

        home = extract_player(m["home"]["name"])
        away = extract_player(m["away"]["name"])

        wr_h = get_winrate(home)
        wr_a = get_winrate(away)
        diff = abs(wr_h - wr_a)

        if diff <= WINRATE_DIFF_THRESHOLD:
            continue

        if wr_h > wr_a:
            high, low = home, away
            high_wr, low_wr = wr_h, wr_a
        else:
            high, low = away, home
            high_wr, low_wr = wr_a, wr_h

        if high_wr <= MIN_HIGH_WR or low_wr >= MAX_LOW_WR:
            continue

        if get_last_result(high) != "W":
            continue

        # ========== GỬI ALERT ==========
        start_local = datetime.fromtimestamp(start_time).strftime('%H:%M:%S')

        message = f"""
🚨 <b>KÈO TỐT SẮP BẮT ĐẦU</b>

⚽ <b>{high}</b> vs {low}
📊 Winrate: <b>{high_wr}%</b> vs {low_wr}%
🔥 Trận gần nhất: <b>Thắng</b>

⏰ Bắt đầu: <b>{start_local}</b> (còn ~30s)
🆔 Match ID: <code>{mid}</code>
        """.strip()

        logger.info(f"THỎA MÃN UPCOMING → {high} ({high_wr}%) vs {low} ({low_wr}%) | Bắt đầu lúc {start_local}")
        send_telegram(message)

# ================= MAIN =================
if __name__ == "__main__":
    initialize_historical_data()
    logger.info("🤖 Bot Upcoming E-Soccer Volta đã khởi động - Gửi alert ~30s trước khi trận bắt đầu")

    while True:
        try:
            update_player_history()
            check_and_alert()
            time.sleep(10)          # Kiểm tra mỗi 10 giây
        except Exception as e:
            logger.error(f"Lỗi vòng lặp: {e}")
            time.sleep(15)