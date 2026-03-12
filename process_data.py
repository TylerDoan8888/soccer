import requests
import time
from datetime import datetime, timedelta
from collections import defaultdict
import firebase_admin
from firebase_admin import credentials, db

# ────────────────────────────────────────────────
# CẤU HÌNH - CHỈNH 2 DÒNG NÀY
API_TOKEN = '247066-ZFfFhtCGjGEUhw'
LEAGUE_ID = '38439'
SPORT_ID = '1'
BASE_URL = 'https://api.betsapi.com/v1'

FIREBASE_CRED_PATH = r'D:\eF\firebase-volta-adminsdk.json'
DATABASE_URL = 'https://volta-ef-default-rtdb.asia-southeast1.firebasedatabase.app/'  # ← THAY BẰNG URL THỰC CỦA BẠN

# Telegram config
TELEGRAM_TOKEN = "7513782443:AAFrjqMeCEJ7NzC3m5RCwxZtqk9n0pyovKM"
CHAT_ID = "5559311100"
TELEGRAM_API = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
WINRATE_DIFF_THRESHOLD = 10.0  # % chênh lệch để gửi thông báo

# Thêm biến toàn cục để cache trận đã gửi cảnh báo
sent_alerts = set()  # lưu dạng: "home_player_vs_away_player_time"

POLL_INTERVAL = 60  # giây

def send_telegram_message(text):
    try:
        payload = {
            'chat_id': CHAT_ID,
            'text': text,
            'parse_mode': 'HTML'  # Để in đậm, link, v.v. nếu cần
        }
        response = requests.post(TELEGRAM_API, data=payload, timeout=10)
        if response.status_code == 200:
            print(f"Đã gửi thông báo Telegram: {text}")
        else:
            print(f"Lỗi gửi Telegram: {response.text}")
    except Exception as e:
        print(f"Lỗi gửi Telegram: {e}")

# Khởi tạo Firebase
cred = credentials.Certificate(FIREBASE_CRED_PATH)
firebase_admin.initialize_app(cred, {'databaseURL': DATABASE_URL})

# Biến lưu trữ
player_stats = defaultdict(lambda: {'win': 0, 'draw': 0, 'lose': 0, 'matches': []})
team_stats   = defaultdict(lambda: {'win': 0, 'draw': 0, 'lose': 0, 'matches': []})
processed_ids = set()

def extract_team(full):
    return full.split('(')[0].strip() if '(' in full else full.strip()

def extract_player(full):
    return full.split('(')[1].rstrip(')').strip() if '(' in full and ')' in full else full.strip()

def process_match(m):
    eid = m.get('id')
    if eid in processed_ids or m.get('time_status') != '3':
        return False
    
    h_full = m['home']['name']
    a_full = m['away']['name']
    h_team = extract_team(h_full)
    a_team = extract_team(a_full)
    h_player = extract_player(h_full)
    a_player = extract_player(a_full)
    
    try:
        hg, ag = map(int, m.get('ss', '0-0').split('-'))
    except:
        return False
    
    if hg > ag:
        player_stats[h_player]['win'] += 1
        player_stats[a_player]['lose'] += 1
        team_stats[h_team]['win'] += 1
        team_stats[a_team]['lose'] += 1
    elif ag > hg:
        player_stats[a_player]['win'] += 1
        player_stats[h_player]['lose'] += 1
        team_stats[a_team]['win'] += 1
        team_stats[h_team]['lose'] += 1
    else:
        player_stats[h_player]['draw'] += 1
        player_stats[a_player]['draw'] += 1
        team_stats[h_team]['draw'] += 1
        team_stats[a_team]['draw'] += 1
    
    desc = f"{h_full} {hg}-{ag} {a_full}"
    player_stats[h_player]['matches'].append(desc)
    player_stats[a_player]['matches'].append(desc)
    team_stats[h_team]['matches'].append(desc)
    team_stats[a_team]['matches'].append(desc)
    
    processed_ids.add(eid)
    print(f"Xử lý trận: {desc} (ID: {eid})")
    return True

def get_sorted_data():
    def prep(d):
        total = d['win'] + d['draw'] + d['lose']
        wr = round(d['win'] / total * 100, 1) if total > 0 else 0.0
        last3 = d['matches'][-3:] if len(d['matches']) >= 3 else d['matches']
        return {'win': d['win'], 'draw': d['draw'], 'lose': d['lose'], 'total': total, 'winrate': wr, 'last3': last3}
    
    p_sorted = sorted(player_stats.items(), key=lambda x: x[1]['win'] / max(1, x[1]['win']+x[1]['draw']+x[1]['lose']), reverse=True)
    t_sorted = sorted(team_stats.items(), key=lambda x: x[1]['win'] / max(1, x[1]['win']+x[1]['draw']+x[1]['lose']), reverse=True)
    
    players = [{'name': k, **prep(v)} for k, v in p_sorted]
    teams   = [{'name': k, **prep(v)} for k, v in t_sorted]
    return players, teams

def upload(upcoming):
    players, teams = get_sorted_data()
    upcoming_list = []
    
    # Tạo map tra cứu winrate nhanh từ players
    player_winrate_map = {p['name']: p['winrate'] for p in players}
    
    # Chỉ kiểm tra 2 trận sắp tới gần nhất (top 2)
    top2_upcoming = upcoming[:2]  # Lấy 2 trận đầu (giả sử API sort theo thời gian tăng dần)
    
    for m in top2_upcoming:
        home_player = extract_player(m['home']['name'])
        away_player = extract_player(m['away']['name'])
        
        wr_home = player_winrate_map.get(home_player, 0.0)
        wr_away = player_winrate_map.get(away_player, 0.0)
        
        diff = abs(wr_home - wr_away)
        if diff >= WINRATE_DIFF_THRESHOLD:
            # Tạo key unique: người chơi + thời gian trận
            match_key = f"{home_player}_{away_player}_{m.get('time', '')}"
            
            if match_key not in sent_alerts:
                higher_player = home_player if wr_home > wr_away else away_player
                lower_player = away_player if wr_home > wr_away else home_player
                higher_wr = max(wr_home, wr_away)
                lower_wr = min(wr_home, wr_away)
                
                message = (
                    f"⚠️ Cảnh báo chênh lệch winrate lớn (2 trận gần nhất)!\n"
                    f"Trận: {m['home']['name']} vs {m['away']['name']}\n"
                    f"Thời gian: {datetime.fromtimestamp(int(m.get('time', 0))).strftime('%Y-%m-%d %H:%M')}\n"
                    f"{higher_player} ({higher_wr}%) vs {lower_player} ({lower_wr}%)\n"
                    f"Chênh lệch: {diff:.1f}%"
                )
                send_telegram_message(message)
                
                # Đánh dấu đã gửi cho trận này
                sent_alerts.add(match_key)
                print(f"Đã gửi alert Telegram cho trận gần nhất: {match_key}")
        
        # Tiếp tục thêm vào upcoming_list (để đẩy lên Firebase như cũ)
        h = m['home']['name']
        a = m['away']['name']
        t_str = datetime.fromtimestamp(int(m.get('time', 0))).strftime('%Y-%m-%d %H:%M')
        upcoming_list.append({
            'time': t_str,
            'home': h, 'away': a,
            'home_player': extract_player(h),
            'away_player': extract_player(a),
            'home_team': extract_team(h),
            'away_team': extract_team(a)
        })
    
    data = {
        'players': players,
        'teams': teams,
        'upcoming': upcoming_list,
        'last_updated': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    }
    db.reference('/').set(data)
    print(f"[{datetime.now().strftime('%H:%M:%S')}] Đã cập nhật Firebase")
    
def poll():
    updated = False
    for ep in ['inplay', 'upcoming', 'ended']:
        url = f"{BASE_URL}/events/{ep}?sport_id={SPORT_ID}&league_id={LEAGUE_ID}&token={API_TOKEN}"
        try:
            r = requests.get(url, timeout=10).json()
            for m in r.get('results', []):
                if process_match(m):
                    updated = True
        except Exception as e:
            print(f"Lỗi poll {ep}: {e}")
    
    today = datetime.now().strftime('%Y%m%d')
    url_today = f"{BASE_URL}/events/date?day={today}&sport_id={SPORT_ID}&league_id={LEAGUE_ID}&token={API_TOKEN}"
    try:
        r = requests.get(url_today, timeout=10).json()
        for m in r.get('results', []):
            if process_match(m):
                updated = True
    except Exception as e:
        print(f"Lỗi poll today: {e}")
    
    return updated

def initialize_historical_data():
    print("Khởi tạo lịch sử 3 tháng... (có thể mất vài phút)")
    end_date = datetime.now()
    start_date = end_date - timedelta(days=90)
    current = start_date
    count = 0
    
    while current <= end_date:
        day_str = current.strftime('%Y%m%d')
        url = f"{BASE_URL}/events/date?day={day_str}&sport_id={SPORT_ID}&league_id={LEAGUE_ID}&token={API_TOKEN}"
        try:
            r = requests.get(url, timeout=15).json()
            matches = r.get('results', [])
            for m in matches:
                if process_match(m):
                    count += 1
            print(f"Ngày {day_str}: {len(matches)} trận, tổng: {count}")
        except Exception as e:
            print(f"Lỗi ngày {day_str}: {e}")
        current += timedelta(days=1)
    
    print(f"Hoàn tất khởi tạo: {count} trận lịch sử đã xử lý.")

# ────────────────────────────────────────────────
# CHẠY KHỞI TẠO LỊCH SỬ (chỉ lần đầu)
initialize_historical_data()

print("\nBắt đầu theo dõi trận mới liên tục...")
print("Nhấn Ctrl+C để dừng")

while True:
    try:
        has_new = poll()
        upcoming_resp = requests.get(
            f"{BASE_URL}/events/upcoming?sport_id={SPORT_ID}&league_id={LEAGUE_ID}&token={API_TOKEN}",
            timeout=10
        ).json()
        upcoming = upcoming_resp.get('results', [])
        upload(upcoming)
        time.sleep(POLL_INTERVAL)
    except KeyboardInterrupt:
        print("Dừng script.")
        break
    except Exception as e:
        print(f"Lỗi vòng lặp: {e}")
        time.sleep(60)