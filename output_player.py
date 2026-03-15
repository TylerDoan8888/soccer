import requests
import time
from datetime import datetime, timedelta
from collections import defaultdict

# ────────────────────────────────────────────────
# CẤU HÌNH
API_TOKEN = '247066-ZFfFhtCGjGEUhw'
LEAGUE_ID = '38439'
SPORT_ID = '1'

# INPLAY dùng BetsAPI (GIỮ NGUYÊN)
INPLAY_BASE_URL = 'https://api.betsapi.com/v1'

# HISTORICAL dùng B365 API (QUAN TRỌNG)
HISTORY_BASE_URL = 'https://api.b365api.com/v3'

# Telegram
TELEGRAM_TOKEN = "7513782443:AAFrjqMeCEJ7NzC3m5RCwxZtqk9n0pyovKM"
CHAT_ID = "5559311100"
TELEGRAM_API = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"

POLL_INTERVAL = 60  # giây

# ─── FORM WINDOW ───
FORM_DAYS = 30       # CHỈ LẤY 30 NGÀY
MIN_MATCHES = 5

# ─── ĐIỀU KIỆN BET ───
WINRATE_DIFF_THRESHOLD = 15.0
MIN_HIGH_WR = 50.0
MAX_LOW_WR = 40.0

# ────────────────────────────────────────────────
# BIẾN LƯU TRỮ
player_stats = defaultdict(lambda: {
    'win': 0,
    'draw': 0,
    'lose': 0,
    'matches': []
})

processed_ids = set()
sent_alerts = set()

# ────────────────────────────────────────────────
def extract_player(full):
    return full.split('(')[1].rstrip(')').strip() if '(' in full and ')' in full else full.strip()

# ────────────────────────────────────────────────
def process_match(m):
    eid = m.get('id')
    if eid in processed_ids:
        return False

    # chỉ xử lý trận đã kết thúc
    if m.get('time_status') not in ('3', 3, None):
        return False

    h_full = m['home']['name']
    a_full = m['away']['name']
    h_player = extract_player(h_full)
    a_player = extract_player(a_full)

    try:
        hg, ag = map(int, m.get('ss', '0-0').split('-'))
    except:
        return False

    if hg > ag:
        player_stats[h_player]['win'] += 1
        player_stats[a_player]['lose'] += 1
    elif ag > hg:
        player_stats[a_player]['win'] += 1
        player_stats[h_player]['lose'] += 1
    else:
        player_stats[h_player]['draw'] += 1
        player_stats[a_player]['draw'] += 1

    desc = f"{h_full} {hg}-{ag} {a_full}"
    player_stats[h_player]['matches'].append(desc)
    player_stats[a_player]['matches'].append(desc)

    processed_ids.add(eid)
    return True

# ────────────────────────────────────────────────
def send_telegram_message(text):
    try:
        requests.post(
            TELEGRAM_API,
            data={'chat_id': CHAT_ID, 'text': text},
            timeout=10
        )
    except Exception as e:
        print(f"Lỗi Telegram: {e}")

# ────────────────────────────────────────────────
def poll_and_check_inplay():
    url = f"{INPLAY_BASE_URL}/events/inplay?sport_id={SPORT_ID}&league_id={LEAGUE_ID}&token={API_TOKEN}"
    try:
        r = requests.get(url, timeout=10).json()
        inplay_matches = r.get('results', [])

        print(f"[{datetime.now().strftime('%H:%M:%S')}] INPLAY: {len(inplay_matches)} trận")

        # xử lý trận vừa kết thúc
        for m in inplay_matches:
            process_match(m)

        # tính winrate
        player_wr = {}
        for p, s in player_stats.items():
            total = s['win'] + s['draw'] + s['lose']
            if total >= MIN_MATCHES:
                player_wr[p] = round(s['win'] / total * 100, 1)
            else:
                player_wr[p] = 0.0

        found = False

        for m in inplay_matches:
            if m.get('time_status') != '1':
                continue

            home_player = extract_player(m['home']['name'])
            away_player = extract_player(m['away']['name'])

            wr_home = player_wr.get(home_player, 0.0)
            wr_away = player_wr.get(away_player, 0.0)

            diff = abs(wr_home - wr_away)
            if diff <= WINRATE_DIFF_THRESHOLD:
                continue

            if wr_home > wr_away:
                high_player, high_wr = home_player, wr_home
                low_player, low_wr = away_player, wr_away
            else:
                high_player, high_wr = away_player, wr_away
                low_player, low_wr = home_player, wr_home

            if high_wr <= MIN_HIGH_WR or low_wr >= MAX_LOW_WR:
                continue

            last_matches = player_stats[high_player]['matches']
            if not last_matches:
                continue

            latest_match = last_matches[-1]
            try:
                score = latest_match.split(' ')[1]
                hg, ag = map(int, score.split('-'))
                high_won_last = hg > ag or ag > hg
            except:
                continue

            if not high_won_last:
                continue

            found = True
            match_key = f"{high_player}_{low_player}_{m.get('id')}"

            output = (
                f"⚠️ CƠ HỘI BET INPLAY\n"
                f"Trận: {m['home']['name']} vs {m['away']['name']}\n"
                f"Người cao WR: {high_player} ({high_wr}%)\n"
                f"Người thấp WR: {low_player} ({low_wr}%)\n"
                f"Chênh lệch: {diff}%"
            )

            print(output)

            if match_key not in sent_alerts:
                send_telegram_message(output)
                sent_alerts.add(match_key)

        if not found:
            print("Không có trận nào thỏa điều kiện.")

    except Exception as e:
        print(f"Lỗi poll: {e}")

# ────────────────────────────────────────────────
def initialize_historical_data():
    print(f"Khởi tạo dữ liệu {FORM_DAYS} ngày gần nhất (ENDED)...")

    end_date = datetime.now()
    start_date = end_date - timedelta(days=FORM_DAYS)

    total = 0
    current = start_date

    while current <= end_date:
        day_str = current.strftime('%Y%m%d')

        url = (
            f"{HISTORY_BASE_URL}/events/ended"
            f"?sport_id={SPORT_ID}"
            f"&league_id={LEAGUE_ID}"
            f"&token={API_TOKEN}"
            f"&day={day_str}"
        )

        try:
            r = requests.get(url, timeout=15).json()
            matches = r.get('results', [])

            for m in matches:
                if process_match(m):
                    total += 1

            print(f"Ngày {day_str}: {len(matches)} trận")

        except Exception as e:
            print(f"Lỗi ngày {day_str}: {e}")

        time.sleep(0.8)
        current += timedelta(days=1)

    print(f"Hoàn tất khởi tạo: {total} trận / {FORM_DAYS} ngày")

# ────────────────────────────────────────────────
# MAIN
initialize_historical_data()

print("\nBắt đầu theo dõi INPLAY...")
print("Nhấn Ctrl+C để dừng")

while True:
    try:
        poll_and_check_inplay()
        time.sleep(POLL_INTERVAL)
    except KeyboardInterrupt:
        print("Dừng script.")
        break
    except Exception as e:
        print(f"Lỗi vòng lặp: {e}")
        time.sleep(60)