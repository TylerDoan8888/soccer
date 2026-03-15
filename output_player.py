import requests
import time
from datetime import datetime, timedelta
from collections import defaultdict

# ────────────────────────────────────────────────
# CẤU HÌNH
API_TOKEN = '247066-ZFfFhtCGjGEUhw'
LEAGUE_ID = '38439'
SPORT_ID = '1'

# INPLAY (BetsAPI)
INPLAY_BASE_URL = 'https://api.betsapi.com/v1'

# HISTORICAL (B365 API)
HISTORY_BASE_URL = 'https://api.b365api.com/v3'

POLL_INTERVAL = 60  # giây

# ─── FORM WINDOW ───
FORM_DAYS = 2
MIN_MATCHES = 5

# ─── ĐIỀU KIỆN BET ───
WINRATE_DIFF_THRESHOLD = 15.0
MIN_HIGH_WR = 43.0
MAX_LOW_WR = 40.0

checked_inplay_ids = set()

# ────────────────────────────────────────────────
# BIẾN LƯU TRỮ
player_stats = defaultdict(lambda: {
    'win': 0,
    'draw': 0,
    'lose': 0,
    'matches': []   # list of dict
})

processed_ids = set()

# ────────────────────────────────────────────────
def extract_player(full):
    return full.split('(')[1].rstrip(')').strip() if '(' in full and ')' in full else full.strip()

# ────────────────────────────────────────────────
def get_last_result(player):
    """
    Trả về: W / L / D / N/A
    """
    matches = player_stats[player]['matches']
    if not matches:
        return 'N/A'

    last = matches[-1]

    hg = last['hg']
    ag = last['ag']
    is_home = last['home']

    if hg == ag:
        return 'D'

    if is_home:
        return 'W' if hg > ag else 'L'
    else:
        return 'W' if ag > hg else 'L'

# ────────────────────────────────────────────────
def process_match(m):
    eid = m.get('id')
    if not eid or eid in processed_ids:
        return False

    # chỉ xử lý trận đã kết thúc
    if m.get('time_status') not in ('3', 3):
        return False

    try:
        h_full = m['home']['name']
        a_full = m['away']['name']

        h_player = extract_player(h_full)
        a_player = extract_player(a_full)

        hg, ag = map(int, m.get('ss', '0-0').split('-'))
    except:
        return False

    # update win / draw / lose
    if hg > ag:
        player_stats[h_player]['win'] += 1
        player_stats[a_player]['lose'] += 1
    elif ag > hg:
        player_stats[a_player]['win'] += 1
        player_stats[h_player]['lose'] += 1
    else:
        player_stats[h_player]['draw'] += 1
        player_stats[a_player]['draw'] += 1

    # lưu match có cấu trúc
    ts = int(m.get('time', 0))

    player_stats[h_player]['matches'].append({
        'opponent': a_player,
        'home': True,
        'hg': hg,
        'ag': ag,
        'ts': ts
    })

    player_stats[a_player]['matches'].append({
        'opponent': h_player,
        'home': False,
        'hg': hg,
        'ag': ag,
        'ts': ts
    })

    processed_ids.add(eid)
    return True

# ────────────────────────────────────────────────
def initialize_historical_data():
    print(f"\n📥 KHỞI TẠO DỮ LIỆU {FORM_DAYS} NGÀY GẦN NHẤT\n")

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

    print(f"\n✅ HOÀN TẤT: {total} trận trong {FORM_DAYS} ngày\n")

    print("📊 SAMPLE PLAYER STATS:")
    for p, s in list(player_stats.items())[:5]:
        total_p = s['win'] + s['draw'] + s['lose']
        if total_p == 0:
            continue
        wr = round(s['win'] / total_p * 100, 1)
        print(f"  {p}: WR={wr}% | Last={get_last_result(p)}")

# ────────────────────────────────────────────────
def poll_and_check_inplay():
    url = f"{INPLAY_BASE_URL}/events/inplay?sport_id={SPORT_ID}&league_id={LEAGUE_ID}&token={API_TOKEN}"
    try:
        r = requests.get(url, timeout=10).json()
        inplay_matches = r.get('results', [])

        print(f"\n[{datetime.now().strftime('%H:%M:%S')}] INPLAY: {len(inplay_matches)} trận")

        # cập nhật nếu có trận vừa kết thúc
        for m in inplay_matches:
            process_match(m)

        # tính winrate
        player_wr = {}
        for p, s in player_stats.items():
            total = s['win'] + s['draw'] + s['lose']
            player_wr[p] = round(s['win'] / total * 100, 1) if total >= MIN_MATCHES else 0.0

        for m in inplay_matches:
            if m.get('time_status') != '1':
                continue

            match_id = m.get('id')
            if not match_id:
                continue

            if match_id in checked_inplay_ids:
                continue

            checked_inplay_ids.add(match_id)

            home_player = extract_player(m['home']['name'])
            away_player = extract_player(m['away']['name'])

            wr_home = player_wr.get(home_player, 0.0)
            wr_away = player_wr.get(away_player, 0.0)

            last_home = get_last_result(home_player)
            last_away = get_last_result(away_player)

            print("-" * 80)
            print(f"INPLAY: {m['home']['name']} vs {m['away']['name']}")
            print(f" HOME: {home_player} | WR={wr_home}% | Last={last_home}")
            print(f" AWAY: {away_player} | WR={wr_away}% | Last={last_away}")

            diff = abs(wr_home - wr_away)
            print(f" DIFF WR: {diff}%")

            if diff <= WINRATE_DIFF_THRESHOLD:
                print(" ❌ Loại: Chênh lệch WR không đủ")
                continue

            if wr_home > wr_away:
                high_player, high_wr = home_player, wr_home
                low_player, low_wr = away_player, wr_away
            else:
                high_player, high_wr = away_player, wr_away
                low_player, low_wr = home_player, wr_home

            if high_wr <= MIN_HIGH_WR:
                print(" ❌ Loại: High WR quá thấp")
                continue

            if low_wr >= MAX_LOW_WR:
                print(" ❌ Loại: Low WR quá cao")
                continue

            if get_last_result(high_player) != 'W':
                print(" ❌ Loại: High player không thắng trận gần nhất")
                continue

            print(" ✅ THỎA ĐIỀU KIỆN BET")

    except Exception as e:
        print(f"Lỗi poll inplay: {e}")

# ────────────────────────────────────────────────
# MAIN
initialize_historical_data()

print("\n🚀 BẮT ĐẦU THEO DÕI INPLAY (Ctrl+C để dừng)\n")

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