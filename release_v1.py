import time
import logging
import requests
from collections import defaultdict
from datetime import datetime, timedelta

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.common.action_chains import ActionChains
from selenium.common.exceptions import (
    StaleElementReferenceException,
    TimeoutException,
    ElementClickInterceptedException,
    NoSuchElementException
)
from selenium.webdriver.common.keys import Keys

# ================= LOGGER =================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger(__name__)

# ================= CONFIG =================
WEB_URL = "https://prod20091.fxf774.com/vi/asian-view/live/B%C3%B3ng-%C4%91%C3%A1?operatorToken=43-bf8a0b751463efee7420af45cf6bf8a1"

API_TOKEN = "247066-ZFfFhtCGjGEUhw"
LEAGUE_ID = '38439'           # E-Soccer Volta league ID

# history / result - API v3
B365_API_BASE = "https://api.b365api.com/v3"

# inplay (vẫn dùng v1)
BETSAPI_BASE = "https://api.betsapi.com/v1"
SPORT_ID = "1"

TARGET_LEAGUE_KEYWORD = "E-Soccer Volta"

FORM_DAYS = 5
MIN_MATCHES = 5

MIN_HIGH_WR = 30.0
MAX_LOW_WR = 60.0
WINRATE_DIFF_THRESHOLD = 15.0

BASE_STAKE = 40
current_stake = BASE_STAKE
MAX_STAKE = 3300

# ================= GLOBAL STATE =================
bet_done_match_ids = set()

player_history = defaultdict(lambda: {
    "win": 0,
    "draw": 0,
    "lose": 0,
    "matches": []
})

processed_ids = set()

last_bet = {
    "match_id": None,
    "player": None,
    "is_home": None
}

# ================= UTIL =================
def extract_player(full):
    full = full.strip()
    logger.debug(f"Raw name: '{full}'")

    if full.endswith("Esports"):
        full = full[:-7].strip()
    if full.endswith(") Esports"):
        full = full[:-9].strip()

    if '(' in full and ')' in full:
        start = full.rfind('(')
        end = full.rfind(')')
        if start != -1 and end != -1 and end > start:
            player_id = full[start+1:end].strip()
            logger.debug(f" → Extracted player ID: '{player_id}'")
            return player_id

    logger.debug(f" → No parentheses → use cleaned: '{full}'")
    return full

# ================= POPUP =================
def close_popup_by_center_click(driver, wait_time=4):
    logger.debug("Thử đóng popup bằng click giữa màn hình")
    time.sleep(wait_time)
    w = driver.execute_script("return window.innerWidth")
    h = driver.execute_script("return window.innerHeight")
    actions = ActionChains(driver)
    actions.move_by_offset(w//2, h//2).click().perform()
    actions.move_by_offset(-(w//2), -(h//2)).perform()
    logger.debug("Đã thử click giữa màn hình")

# ================= API – HISTORY (v3) =================
def fetch_finished_matches(day=None, page=1, league_id=LEAGUE_ID):
    params = f"?token={API_TOKEN}&sport_id={SPORT_ID}&page={page}"
    if day:
        params += f"&day={day}"
    if league_id:
        params += f"&league_id={league_id}"

    url = f"{B365_API_BASE}/events/ended{params}"
    logger.debug(f"Fetch ended v3 page {page}: {url}")

    try:
        resp = requests.get(url, timeout=12)
        resp.raise_for_status()
        data = resp.json()

        results = data.get("results", [])
        pager = data.get("pager", {})
        total_pages = pager.get("total_pages", 1)

        return results, total_pages

    except requests.exceptions.RequestException as e:
        logger.error(f"Fetch ended v3 error (page {page}): {e}")
        return [], 1
    except ValueError as e:
        logger.error(f"JSON parse error: {e}")
        return [], 1

def initialize_historical_data():
    logger.info(f"===== KHỞI TẠO DỮ LIỆU {FORM_DAYS} NGÀY (API v3) =====")
    end_date = datetime.now()
    start_date = end_date - timedelta(days=FORM_DAYS)
    
    total_matches_processed = 0
    unique_players = set()

    current = start_date
    while current <= end_date:
        day_str = current.strftime("%Y%m%d")
        logger.info(f"Xử lý ngày: {day_str}")

        page = 1
        total_pages = 1

        while page <= total_pages:
            matches, total_pages = fetch_finished_matches(day=day_str, page=page)

            day_processed = 0
            for m in matches:
                if process_match(m):
                    day_processed += 1
                    total_matches_processed += 1
                    home = extract_player(m["home"]["name"])
                    away = extract_player(m["away"]["name"])
                    unique_players.add(home)
                    unique_players.add(away)

            logger.info(f"  Trang {page}/{total_pages} → xử lý {day_processed} trận")
            page += 1
            time.sleep(0.9)  # tránh rate limit

        current += timedelta(days=1)
        time.sleep(0.7)

    logger.info("===== KHỞI TẠO HOÀN TẤT =====")
    logger.info(f"Tổng trận xử lý: {total_matches_processed}")
    logger.info(f"Player unique: {len(unique_players)}")

    # In top players để kiểm tra
    top_players = sorted(
        player_history.items(),
        key=lambda x: len(x[1]["matches"]),
        reverse=True
    )[:10]

    logger.info("Top 10 players (số trận nhiều nhất):")
    for p, data in top_players:
        total = data["win"] + data["draw"] + data["lose"]
        wr = round(data["win"] / total * 100, 1) if total > 0 else 0.0
        logger.info(f"  {p:28} | {total:4} trận | W/D/L = {data['win']:3}/{data['draw']:3}/{data['lose']:3} | WR={wr:5.1f}%")

def process_match(m):
    eid = m.get('id')
    if not eid:
        return False
    if eid in processed_ids:
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

    home_raw = m["home"]["name"]
    away_raw = m["away"]["name"]
    home = extract_player(home_raw)
    away = extract_player(away_raw)

    logger.debug(f"XỬ LÝ {eid} | {home} vs {away} | {hg}-{ag}")

    if hg > ag:
        player_history[home]["win"] += 1
        player_history[away]["lose"] += 1
    elif ag > hg:
        player_history[away]["win"] += 1
        player_history[home]["lose"] += 1
    else:
        player_history[home]["draw"] += 1
        player_history[away]["draw"] += 1

    player_history[home]["matches"].append({"home": True,  "hg": hg, "ag": ag, "ts": int(m.get("time", 0))})
    player_history[away]["matches"].append({"home": False, "hg": hg, "ag": ag, "ts": int(m.get("time", 0))})

    processed_ids.add(eid)
    return True

def update_player_history():
    new_count = 0
    matches, _ = fetch_finished_matches(page=1)
    for m in matches:
        if process_match(m):
            new_count += 1

def get_winrate(player):
    if player not in player_history:
        return 0.0

    s = player_history[player]
    total = s["win"] + s["draw"] + s["lose"]
    if total < MIN_MATCHES:
        return 0.0
    if total == 0:
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

# ================= INPLAY =================
def fetch_inplay_matches_betsapi():
    url = f"{B365_API_BASE}/events/inplay?sport_id={SPORT_ID}&league_id={LEAGUE_ID}&token={API_TOKEN}"
    # Hoặc nếu muốn lấy tất cả soccer rồi lọc thủ công: bỏ league_id đi
    # url = f"{B365_API_BASE}/events/inplay?sport_id={SPORT_ID}&token={API_TOKEN}"

    try:
        r = requests.get(url, timeout=8).json()
        if r.get("success") == 0:
            logger.error(f"API error: {r.get('error')}")
            return []

        matches = r.get("results", [])
        
        valid = []
        for m in matches:
            if str(m.get("time_status")) != "1":  # chỉ lấy đang diễn ra
                continue
            ss = m.get("ss", "")
            if not ss or ss.strip() in ["", "0-0", "*"]:  # loại bỏ trận chưa có tỷ số hoặc invalid
                continue
            valid.append(m)
        
        return valid

    except Exception as e:
        logger.error(f"Inplay v3 fetch error: {e}")
        return []
    
def get_best_inplay_candidate():
    matches = fetch_inplay_matches_betsapi()
    if not matches:
        return None

    for m in matches:
        mid = m.get("id")
        if not mid or mid in bet_done_match_ids:
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
            is_home_high = True
        else:
            high, low = away, home
            high_wr, low_wr = wr_a, wr_h
            is_home_high = False

        if high_wr <= MIN_HIGH_WR or low_wr >= MAX_LOW_WR:
            continue

        if get_last_result(high) != "W":
            continue

        logger.info(f"THỎA MÃN → Bet {high} ({high_wr}%) vs {low} ({low_wr}%)")
        return {
            "match_id": mid,
            "player": high,
            "is_home": is_home_high
        }

    return None

# ================= SELENIUM FUNCTIONS =================
# (giữ nguyên như bạn đã cung cấp, chỉ thêm một số try-except nhỏ nếu cần)

def find_volta_league():
    leagues = driver.find_elements(
        By.CSS_SELECTOR,
        "div.eventlist_asia_fe_EventListLeague_container"
    )
    for l in leagues:
        try:
            name = l.find_element(
                By.CSS_SELECTOR,
                "h3.eventlist_asia_fe_EventListLeague_leagueName span"
            ).text
            if TARGET_LEAGUE_KEYWORD in name:
                return l
        except:
            pass
    return None

def ensure_league_expanded(league_element, timeout=6):
    try:
        events = league_element.find_elements(
            By.CSS_SELECTOR,
            "div.eventlist_asia_fe_EventListLeague_singleEvent"
        )
        if events:
            return True

        header = WebDriverWait(league_element, timeout).until(
            EC.element_to_be_clickable(
                (By.CSS_SELECTOR, "div.eventlist_asia_fe_EventListLeague_headerWrapper")
            )
        )
        header.click()

        WebDriverWait(league_element, timeout).until(
            lambda d: d.find_elements(
                By.CSS_SELECTOR,
                "div.eventlist_asia_fe_EventListLeague_singleEvent"
            )
        )
        return True
    except Exception as e:
        logger.warning(f"Không mở được league: {e}")
        return False

def open_event_page_by_player(driver, player_name, max_retry=3, timeout=10):
    for attempt in range(max_retry):
        try:
            WebDriverWait(driver, timeout).until(
                EC.presence_of_element_located(
                    (By.CSS_SELECTOR, "div.eventlist_asia_fe_EventListLeague_singleEvent")
                )
            )

            events = driver.find_elements(
                By.CSS_SELECTOR,
                "div.eventlist_asia_fe_EventListLeague_singleEvent"
            )

            for event in events:
                teams = event.find_elements(
                    By.CSS_SELECTOR,
                    "span.eventlist_asia_fe_EventCard_teamNameText"
                )
                team_names = [t.text.strip() for t in teams if t.text.strip()]

                if not any(player_name.lower() in t.lower() for t in team_names):
                    continue

                time_cell = WebDriverWait(event, 5).until(
                    EC.element_to_be_clickable(
                        (By.CSS_SELECTOR, "div.eventlist_asia_fe_sharedGrid_timeCell")
                    )
                )

                driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", time_cell)

                try:
                    time_cell.click()
                except:
                    driver.execute_script("arguments[0].click();", time_cell)

                logger.info(f"Đã mở trang trận cho {player_name}")
                return True

        except Exception as e:
            logger.debug(f"Attempt {attempt+1} lỗi: {e}")
            time.sleep(0.6)

    logger.warning(f"Không mở được trang trận cho {player_name}")
    return False

def click_team(player):
    """
    Tìm và click vào button Moneyline tương ứng với player trong trang event.
    Return: True nếu click thành công, False nếu không tìm thấy hoặc fail sau retry.
    """
    logger.info("🔍 Đang tìm và click market Moneyline cho player...")

    try:
        # Wait container market load (đảm bảo Moneyline đã xuất hiện)
        WebDriverWait(driver, 12).until(
            EC.presence_of_element_located(
                (By.CSS_SELECTOR, "div.eventpage_fe_MoneyLine_markets")
            )
        )

        max_attempts = 3
        for attempt in range(max_attempts):
            try:
                buttons = driver.find_elements(
                    By.CSS_SELECTOR,
                    "button.eventpage_fe_MoneyLineSelection_line"
                )
                logger.info(f"🔢 Attempt {attempt+1}: Tìm thấy {len(buttons)} lựa chọn")

                for btn in buttons:
                    try:
                        # Lấy tên player từ sub-element (chính xác hơn lấy toàn button text)
                        name_el = btn.find_element(
                            By.CSS_SELECTOR,
                            "div.eventpage_fe_MoneyLineSelection_meaning"
                        )
                        name = name_el.get_attribute("title") or name_el.text or ""
                        name = name.strip()

                        if player.lower() in name.lower():
                            # Scroll để button vào tầm nhìn
                            driver.execute_script(
                                "arguments[0].scrollIntoView({block: 'center'});",
                                btn
                            )

                            # Wait clickable ngắn
                            WebDriverWait(driver, 3).until(
                                EC.element_to_be_clickable(btn)
                            )

                            try:
                                btn.click()  # Thử native click trước
                            except (ElementClickInterceptedException, StaleElementReferenceException):
                                logger.debug("Native click fail → fallback JS click")
                                driver.execute_script("arguments[0].click();", btn)

                            logger.info(f"🟢 ĐÃ CHỌN: {name} (player: {player})")
                            return True

                    except StaleElementReferenceException:
                        continue  # Skip button stale, thử button tiếp theo
                    except Exception as e:
                        logger.debug(f"Skip button lỗi: {str(e)}")
                        continue

                time.sleep(0.5)  # Delay nhẹ giữa các attempt

            except StaleElementReferenceException:
                logger.debug(f"Stale list buttons attempt {attempt+1} → retry")
                time.sleep(0.8)

        logger.warning(f"❌ Không tìm thấy player '{player}' sau {max_attempts} attempts")
        return False

    except Exception as e:
        logger.warning(f"❌ Lỗi load market Moneyline: {str(e)}")
        return False

def set_stake(amount=50):
    """
    Set số tiền cược vào betslip. Ưu tiên clear button, fallback Ctrl+A + Backspace.
    Return: True nếu thành công, False nếu fail.
    """
    time.sleep(1.2)  # Chờ betslip render ổn định (có thể tăng nếu mạng chậm)

    try:
        stake_input = driver.find_element(
            By.CSS_SELECTOR,
            "input.betslip_fe_CounterSecondary_input"
        )
    except NoSuchElementException:
        logger.warning("❌ Không tìm thấy ô nhập tiền cược")
        return False

    # Ưu tiên click nút Clear nếu tồn tại
    try:
        clear_btn = driver.find_element(
            By.CSS_SELECTOR,
            "button.betslip_fe_CounterSecondary_counter__clearStakeButton"
        )
        clear_btn.click()
        time.sleep(0.3)
        logger.info("🧹 Đã xóa stake cũ bằng nút Clear")
    except (NoSuchElementException, ElementNotInteractableException):
        # Fallback: Ctrl+A + Backspace (simulate user xóa)
        try:
            stake_input.click()
            stake_input.send_keys(Keys.CONTROL + "a")  # Ctrl + A
            stake_input.send_keys(Keys.BACKSPACE)      # Xóa
            time.sleep(0.3)
            logger.info("🧹 Đã xóa stake cũ bằng Ctrl+A + Backspace")
        except Exception as e:
            logger.warning(f"❌ Không xóa được stake cũ: {str(e)}")
            return False

    # Nhập amount mới
    try:
        stake_input.send_keys(str(amount))
        logger.info(f"💰 Đã set stake: {amount}")
        return True
    except Exception as e:
        logger.warning(f"❌ Không nhập được stake {amount}: {str(e)}")
        return False

from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, ElementClickInterceptedException, NoSuchElementException

def place_bet():
    """
    Nhấn nút Place Bet trên betslip Bet365.
    Return: True nếu thành công, False nếu fail.
    """
    time.sleep(1.5)  # Chờ betslip ổn định (odds/stake load xong)

    try:
        # Wait cho button visible & clickable
        place_btn = WebDriverWait(driver, 10).until(
            EC.element_to_be_clickable((By.ID, "place-bets"))
        )

        # Scroll để chắc chắn button trong view
        driver.execute_script(
            "arguments[0].scrollIntoView({block: 'center'});",
            place_btn
        )
        time.sleep(0.4)  # Chờ scroll hoàn tất + animation

        try:
            place_btn.click()  # Native click trước
        except (ElementClickInterceptedException, TimeoutException):
            logger.debug("Native click intercepted → fallback JS click")
            driver.execute_script("arguments[0].click();", place_btn)

        logger.info("🚀 ĐÃ NHẤN NÚT ĐẶT CƯỢC")
        time.sleep(2)  # Chờ confirmation hoặc redirect (tùy bot)
        return True

    except TimeoutException:
        logger.warning("❌ Nút Place Bet không clickable hoặc không xuất hiện (timeout)")
        return False
    except NoSuchElementException:
        logger.warning("❌ Không tìm thấy nút Place Bet (ID: place-bets)")
        return False
    except Exception as e:
        logger.warning(f"❌ Lỗi khi place bet: {str(e)}")
        return False
    
def go_back():
    """
    Quay lại danh sách trận bằng cách click breadcrumb "Back".
    Nếu breadcrumb fail → fallback dùng driver.back().
    Return: True nếu thành công, False nếu fail.
    """
    time.sleep(3)  # Chờ page chi tiết ổn định trước khi back (có thể điều chỉnh)

    try:
        # Wait cho breadcrumb xuất hiện và clickable
        back_btn = WebDriverWait(driver, 8).until(
            EC.element_to_be_clickable(
                (By.CSS_SELECTOR, "div.navigation_eu_fe_Breadcrumbs_link")
            )
        )

        driver.execute_script(
            "arguments[0].scrollIntoView({block: 'center'});",
            back_btn
        )

        try:
            back_btn.click()  # Thử native click trước
        except (ElementClickInterceptedException, TimeoutException):
            logger.debug("Native click intercepted → fallback JS click")
            driver.execute_script("arguments[0].click();", back_btn)

        logger.info("↩️ Đã quay lại danh sách trận")
        time.sleep(2)  # Chờ list events load lại (có thể thay bằng wait cụ thể sau)
        return True

    except TimeoutException:
        logger.warning("❌ Không tìm thấy hoặc không clickable nút Trở lại (timeout)")
    except Exception as e:
        logger.warning(f"❌ Lỗi click breadcrumb: {str(e)}")

    # Fallback: dùng driver.back() nếu breadcrumb không hoạt động
    try:
        driver.back()
        logger.info("↩️ Fallback: dùng driver.back()")
        time.sleep(2)
        return True
    except Exception as e:
        logger.warning(f"❌ Fallback driver.back() cũng fail: {str(e)}")
        return False

# ================= CHECK RESULT =================
def wait_and_check_result():
    global current_stake
    logger.info("⏳ Chờ kết quả trận...")
    time.sleep(5)

    match_id_str = str(last_bet["match_id"])

    while True:
        try:
            matches, total_pages = fetch_finished_matches(page=1)

            for m in matches:
                if not isinstance(m, dict):
                    logger.warning(f"Bỏ qua item không phải dict: {m}")
                    continue

                current_id = str(m.get("id"))
                if current_id != match_id_str:
                    continue

                ss = m.get("ss")
                if not ss or "-" not in ss:
                    logger.warning(f"Trận {current_id} thiếu ss hoặc format sai: {ss}")
                    continue

                try:
                    h, a = map(int, ss.split("-"))
                except:
                    logger.warning(f"Không parse được score: {ss}")
                    continue

                win = (h > a) if last_bet["is_home"] else (a > h)

                if win:
                    logger.info("✅ THẮNG – reset stake")
                    current_stake = BASE_STAKE
                else:
                    logger.warning("❌ THUA – gấp đôi stake")
                    current_stake *= 2
                    if current_stake > MAX_STAKE:
                        logger.error("⛔ Stake quá lớn – DỪNG BOT")
                        exit()

                return  # Thoát hàm sau khi xử lý xong

            logger.debug("Chưa thấy kết quả → chờ thêm...")
            time.sleep(5)

        except Exception as e:
            logger.error(f"Lỗi khi check kết quả: {e}")
            time.sleep(10)

# ================= MAIN =================
if __name__ == "__main__":
    options = Options()
    options.add_argument("--start-maximized")
    options.add_argument("--disable-blink-features=AutomationControlled")
    driver = webdriver.Chrome(options=options)
    driver.get(WEB_URL)
    close_popup_by_center_click(driver)

    initialize_historical_data()

    while True:
        update_player_history()

        candidate = get_best_inplay_candidate()
        if not candidate:
            time.sleep(3)
            continue

        logger.info(f"Chuẩn bị đặt cược: {candidate}")

        league = find_volta_league()
        if not league:
            time.sleep(3)
            continue

        if not ensure_league_expanded(league):
            time.sleep(3)
            continue

        if not open_event_page_by_player(driver, candidate["player"]):
            go_back()
            continue

        if not click_team(candidate["player"]):
            go_back()
            continue

        if not set_stake(current_stake):
            go_back()
            continue

        if not place_bet():
            go_back()
            continue

        logger.info(f"ĐÃ ĐẶT CƯỢC | Stake = {current_stake}")
        bet_done_match_ids.add(candidate["match_id"])
        last_bet.update(candidate)

        go_back()
        wait_and_check_result()
