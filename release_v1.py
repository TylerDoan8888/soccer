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
from selenium.common.exceptions import StaleElementReferenceException
from selenium.common.exceptions import TimeoutException, ElementClickInterceptedException


processed_ids = set()

# ================= LOGGER =================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)
logger = logging.getLogger(__name__)

# ================= CONFIG =================
WEB_URL = "https://prod20091.fxf774.com/vi/asian-view/live/B%C3%B3ng-%C4%91%C3%A1?operatorToken=43-be38e386ed5bcfa2f3bbec8c4d2fea1f"

API_TOKEN = "247066-ZFfFhtCGjGEUhw"
LEAGUE_ID = '38439'

# history / result
B365_API_BASE = "https://api.b365api.com/v3"

# inplay (BẮT BUỘC)
BETSAPI_BASE = "https://api.betsapi.com/v1"

SPORT_ID = "1"

TARGET_LEAGUE_KEYWORD = "E-Soccer Volta"

FORM_DAYS = 2
MIN_MATCHES = 5

MIN_HIGH_WR = 45.0
MAX_LOW_WR = 45.0
WINRATE_DIFF_THRESHOLD = 15.0

BASE_STAKE = 50
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

last_bet = {
    "match_id": None,
    "player": None,
    "is_home": None
}

# ================= UTIL =================
def extract_player(full):
    return full.split('(')[1].rstrip(')').strip() if '(' in full and ')' in full else full.strip()

# ================= POPUP =================
def close_popup_by_center_click(driver, wait_time=4):
    time.sleep(wait_time)
    w = driver.execute_script("return window.innerWidth")
    h = driver.execute_script("return window.innerHeight")
    actions = ActionChains(driver)
    actions.move_by_offset(w//2, h//2).click().perform()
    actions.move_by_offset(-(w//2), -(h//2)).perform()

# ================= API – HISTORY =================
def fetch_finished_matches(day=None):
    params = f"?token={API_TOKEN}&sport_id=1"
    if day:
        params += f"&day={day}"
    
    try:
        r = requests.get(f"{B365_API_BASE}/events/ended{params}", timeout=15).json()
        return r.get("results", [])
    except Exception as e:
        print(f"Lỗi khi lấy dữ liệu cho ngày {day}: {e}")
        return []

def initialize_historical_data():
    print(f"\n📥 KHỞI TẠO DỮ LIỆU {FORM_DAYS} NGÀY GẦN NHẤT\n")

    end_date = datetime.now()
    start_date = end_date - timedelta(days=FORM_DAYS)
    total_matches = 0
    current = start_date

    while current <= end_date:
        day_str = current.strftime("%Y%m%d")
        matches = fetch_finished_matches(day_str)

        for m in matches:
            if process_match(m):
                total_matches += 1

        print(f"Ngày {day_str}: {len(matches)} trận")
        current += timedelta(days=1)
        time.sleep(0.6)

    print(f"\n✅ HOÀN TẤT: {total_matches} trận trong {FORM_DAYS} ngày\n")
    print("📊 SAMPLE PLAYER STATS:")
    for player, stats in list(player_history.items())[:5]:  # Hiển thị 5 cầu thủ đầu tiên
        total_games = stats['win'] + stats['draw'] + stats['lose']
        if total_games == 0:
            continue
        win_rate = round(stats['win'] / total_games * 100, 1)
        print(f"  {player}: WR={win_rate}% | Last={get_last_result(player)}")


def process_match(m):
    eid = m.get('id')
    if not eid or eid in processed_ids:
        return False  # Trả về False nếu ID không hợp lệ hoặc đã được xử lý

    # Chỉ xử lý trận đã kết thúc
    if m.get('time_status') not in ('3', 3):
        return False

    if not m.get("ss"):
        return False

    try:
        hg, ag = map(int, m["ss"].split("-"))
    except ValueError:
        return False

    home = extract_player(m["home"]["name"])
    away = extract_player(m["away"]["name"])
    ts = int(m.get("time", 0))

    if hg > ag:
        player_history[home]["win"] += 1
        player_history[away]["lose"] += 1
    elif ag > hg:
        player_history[away]["win"] += 1
        player_history[home]["lose"] += 1
    else:
        player_history[home]["draw"] += 1
        player_history[away]["draw"] += 1

    player_history[home]["matches"].append(
        {"home": True, "hg": hg, "ag": ag, "ts": ts}
    )
    player_history[away]["matches"].append(
        {"home": False, "hg": hg, "ag": ag, "ts": ts}
    )

    return True  # Trả về True nếu trận đấu được xử lý thành công

def update_player_history():
    for m in fetch_finished_matches():
        process_match(m)

def get_winrate(player):
    s = player_history[player]
    total = s["win"] + s["draw"] + s["lose"]
    if total < MIN_MATCHES:
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

# ================= API – INPLAY (BETSAPI) =================
def fetch_inplay_matches_betsapi():
    try:
        url = f"{BETSAPI_BASE}/events/inplay"
        params = {
            "token": API_TOKEN,
            "sport_id": SPORT_ID,
            "league_id": LEAGUE_ID,          # thêm vào để giống Code 1
        }
        r = requests.get(url, params=params, timeout=10).json()
        
        matches = r.get("results", [])
        
        # Optional: lọc thêm nếu API trả nhầm (hiếm nhưng an toàn)
        valid_matches = []
        for m in matches:
            if str(m.get("time_status")) != "1":
                continue
            # Nếu muốn lọc chặt hơn về "đang thực sự trong game/set"
            ss = m.get("ss")
            if ss is None or ss.strip() in ["", "0-0", "*"]:  # ví dụ loại các trận chưa có điểm
                continue  # hoặc log: "Bỏ qua: chưa có điểm số rõ ràng"
                
            valid_matches.append(m)
        
        return valid_matches
    
    except Exception as e:
        print(f"Lỗi fetch inplay BetsAPI: {e}")
        return []

def get_best_inplay_candidate():
    matches = fetch_inplay_matches_betsapi()
    if not matches:
        return None

    for m in matches:
        if m.get("time_status") != "1":
            continue

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
            is_home = True
        else:
            high, low = away, home
            high_wr, low_wr = wr_a, wr_h
            is_home = False

        if high_wr <= MIN_HIGH_WR:
            continue
        if low_wr >= MAX_LOW_WR:
            continue
        if get_last_result(high) != "W":
            continue

        logger.info(
            f"🎯 BET {high} | WR={high_wr}% vs {low_wr}% | DIFF={diff}%"
        )

        return {
            "match_id": mid,
            "player": high,
            "is_home": is_home
        }

    return None

# ================= SELENIUM =================
options = Options()
options.add_argument("--start-maximized")
options.add_argument("--disable-blink-features=AutomationControlled")

driver = webdriver.Chrome(options=options)
driver.get(WEB_URL)
close_popup_by_center_click(driver)

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

def ensure_league_expanded(league_element, timeout=5):
    """
    Đảm bảo league đã mở rộng để hiển thị events.
    Return: True nếu đã mở (hoặc đã mở sẵn), False nếu fail.
    """
    try:
        # Check xem đã có events chưa
        events = league_element.find_elements(
            By.CSS_SELECTOR,
            "div.eventlist_asia_fe_EventListLeague_singleEvent"
        )
        if events:
            logger.debug("League đã mở sẵn, có events.")
            return True

        # Tìm header để click (chính xác hơn click trực tiếp league_element)
        header = WebDriverWait(league_element, timeout).until(
            EC.element_to_be_clickable(
                (By.CSS_SELECTOR, "div.eventlist_asia_fe_EventListLeague_headerWrapper")
            )
        )
        
        header.click()
        # Wait cho events xuất hiện thay vì sleep cứng
        WebDriverWait(league_element, timeout).until(
            lambda d: d.find_elements(
                By.CSS_SELECTOR,
                "div.eventlist_asia_fe_EventListLeague_singleEvent"
            )
        )
        
        # Optional: lấy tên league để log động
        try:
            league_name = league_element.find_element(
                By.CSS_SELECTOR, ".league-name-or-similar-selector"  # thay bằng selector thật
            ).text.strip()
        except:
            league_name = "Unknown League"
            
        logger.info(f"🔽 Đã xổ giải: {league_name}")
        return True
        
    except (TimeoutException, ElementClickInterceptedException) as e:
        logger.warning(f"⚠️ Không xổ được league: {str(e)}")
        return False
    except Exception as e:
        logger.error(f"❌ Lỗi mở league: {str(e)}")
        return False

from selenium.common.exceptions import StaleElementReferenceException, ElementClickInterceptedException, TimeoutException
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

def open_event_page_by_player(driver, player_name, max_retry=3, timeout=8):
    """
    Tìm trận chứa player_name và click ô thời gian để mở trang trận.
    Return: True nếu thành công, False nếu không tìm thấy hoặc fail sau retry.
    """
    for attempt in range(max_retry):
        try:
            # Wait cho list events ổn định
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
                
                # Scroll & wait clickable
                time_cell = WebDriverWait(event, 5).until(
                    EC.element_to_be_clickable(
                        (By.CSS_SELECTOR, "div.eventlist_asia_fe_sharedGrid_timeCell")
                    )
                )
                
                driver.execute_script(
                    "arguments[0].scrollIntoView({block: 'center'});",
                    time_cell
                )
                
                # Thử native click trước, fallback JS nếu fail
                try:
                    time_cell.click()
                except (ElementClickInterceptedException, TimeoutException):
                    logger.warning("Native click fail → fallback JS click")
                    driver.execute_script("arguments[0].click();", time_cell)
                
                logger.info(f"➡️ Đã click mở trang trận cho player: {player_name}")
                return True
            
            logger.warning(f"Attempt {attempt+1}: Không tìm thấy trận chứa '{player_name}'")
        
        except StaleElementReferenceException:
            logger.debug(f"Stale element ở attempt {attempt+1} → retry sau 0.5s")
            time.sleep(0.5)
            continue
        except Exception as e:
            logger.error(f"Lỗi mở trang trận attempt {attempt+1}: {str(e)}")
            time.sleep(1)
    
    logger.warning(f"❌ Không mở được trang trận cho '{player_name}' sau {max_retry} lần thử")
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
    time.sleep(300)

    while True:
        for m in fetch_finished_matches():
            if str(m.get("id")) != str(last_bet["match_id"]):
                continue

            h, a = map(int, m["ss"].split("-"))
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
            return
        time.sleep(15)

# ================= MAIN =================
initialize_historical_data()

while True:
    update_player_history()

    candidate = get_best_inplay_candidate()
    if not candidate:
        time.sleep(10)
        continue

    league = find_volta_league()
    if not league:
        time.sleep(5)
        continue

    ensure_league_expanded(league)

    if not open_event_page_by_player(candidate["player"]):
        continue

    if not click_team(candidate["player"]):
        go_back()
        continue

    set_stake(current_stake)
    place_bet()

    logger.info(f"🎉 ĐÃ ĐẶT CƯỢC | Stake={current_stake}")

    bet_done_match_ids.add(candidate["match_id"])
    last_bet.update(candidate)

    go_back()
    wait_and_check_result()