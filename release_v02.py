import time
import logging
import requests
from collections import defaultdict
from datetime import datetime, timedelta

# ================= LOGGER =================
logging.basicConfig(
    level=logging.DEBUG,          # Giữ DEBUG để thấy hết log chi tiết
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

ALERT_BEFORE_SECONDS = 30   # Gửi alert trước khi trận bắt đầu

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
    logger.debug(f"Fetching ended: {url}")
    
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

    # Log winrate tất cả player
    logger.info("=== WINRATE TOÀN BỘ PLAYER SAU KHI LOAD HISTORY ===")
    sorted_players = sorted(player_history.items(), key=lambda x: len(x[1]["matches"]), reverse=True)
    for player, data in sorted_players[:30]:
        total = data["win"] + data["draw"] + data["lose"]
        wr = round(data["win"] / total * 100, 1) if total > 0 else 0.0
        last = get_last_result(player)
        logger.info(f"  {player:25} | {total:3} trận | WR={wr:5.1f}% | Last={last} | W/D/L={data['win']:2}/{data['draw']:2}/{data['lose']:2}")

def update_player_history():
    logger.debug("Cập nhật lịch sử mới nhất (page 1)")
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

# ================= UPCOMING & ALERT =================
def fetch_upcoming_matches():
    url = f"{B365_API_BASE}/events/upcoming?sport_id={SPORT_ID}&league_id={LEAGUE_ID}&token={API_TOKEN}"
    logger.debug(f"Fetching upcoming: {url}")
    try:
        r = requests.get(url, timeout=10).json()
        logger.debug(f"Upcoming API trả về {len(r.get('results', []))} trận")
        return r.get("results", [])
    except Exception as e:
        logger.error(f"Upcoming fetch error: {e}")
        return []

def check_and_alert():
    now_unix = int(time.time())
    matches = fetch_upcoming_matches()
    if not matches:
        logger.debug("Không có trận upcoming nào")
        return

    for m in matches:
        mid = m.get("id")
        start_time = int(m.get("time", 0))
        if not mid or start_time == 0:
            continue

        seconds_to_start = start_time - now_unix
        if not (30 <= seconds_to_start <= 600):
            continue

        if abs(seconds_to_start - ALERT_BEFORE_SECONDS) > 12:
            continue

        home = extract_player(m["home"]["name"])
        away = extract_player(m["away"]["name"])
        wr_h = get_winrate(home)
        wr_a = get_winrate(away)
        diff = abs(wr_h - wr_a)

        logger.info(f"Đang kiểm tra upcoming: {home} ({wr_h}%) vs {away} ({wr_a}%) | diff={diff:.1f}% | còn {seconds_to_start}s")

        # ===== KIỂM TRA TỪNG ĐIỀU KIỆN =====
        if diff <= WINRATE_DIFF_THRESHOLD:
            logger.debug(f"→ Skip: Chênh lệch winrate chỉ {diff:.1f}% (<= {WINRATE_DIFF_THRESHOLD}%)")
            continue

        if wr_h > wr_a:
            high, low = home, away
            high_wr, low_wr = wr_h, wr_a
        else:
            high, low = away, home
            high_wr, low_wr = wr_a, wr_h

        if high_wr <= MIN_HIGH_WR:
            logger.debug(f"→ Skip: Winrate cao nhất chỉ {high_wr}% (<= {MIN_HIGH_WR}%)")
            continue

        if low_wr >= MAX_LOW_WR:
            logger.debug(f"→ Skip: Winrate thấp nhất {low_wr}% (>= {MAX_LOW_WR}%)")
            continue

        last_res = get_last_result(high)
        if last_res != "W":
            logger.debug(f"→ Skip: Trận gần nhất của {high} là {last_res} (không phải W)")
            continue

        # ========== THỎA MÃN TOÀN BỘ ĐIỀU KIỆN ==========
        start_local = datetime.fromtimestamp(start_time).strftime('%H:%M:%S')
        logger.info(f"✅ THỎA MÃN TOÀN BỘ ĐIỀU KIỆN → {high} ({high_wr}%) vs {low} ({low_wr}%) | Bắt đầu {start_local}")

        message = f"""
🚨 <b>KÈO TỐT SẮP BẮT ĐẦU</b>

⚽ <b>{high}</b> vs {low}
📊 Winrate: <b>{high_wr}%</b> vs {low_wr}%
🔥 Trận gần nhất: <b>Thắng</b>

⏰ Bắt đầu: <b>{start_local}</b> (còn ~{seconds_to_start}s)
🆔 Match ID: <code>{mid}</code>
        """.strip()

        send_telegram(message)

# ================= MAIN =================
if __name__ == "__main__":
    initialize_historical_data()
    logger.info("🤖 Bot Upcoming E-Soccer Volta đã khởi động (Debug + Log chi tiết ON)")

    while True:
        try:
            update_player_history()
            check_and_alert()
            time.sleep(25)          # ← Tăng lên 25 giây để giảm tải
        except Exception as e:
            logger.error(f"Lỗi vòng lặp chính: {e}")
            time.sleep(20)
