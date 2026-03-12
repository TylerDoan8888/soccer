import time
import logging
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By

# ================= LOGGER =================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)
logger = logging.getLogger(__name__)

# ================= INPUT URL =================
url = input("Nhập link trang web: ").strip()

# ================= CHROME OPTIONS =================
options = Options()
options.add_argument("--start-maximized")
options.add_argument("--disable-blink-features=AutomationControlled")

logger.info("Khởi tạo Chrome WebDriver...")
driver = webdriver.Chrome(options=options)

# ================= OPEN WEB =================
driver.get(url)
input("👉 Setup xong thì nhấn ENTER để chạy tiếp...")

# =================================================
# =============== SAU KHI NHẤN ENTER ===============
# =================================================

TARGET_LEAGUE_KEYWORD = "E-Soccer Volta"


def find_volta_league(driver):
    leagues = driver.find_elements(
        By.CSS_SELECTOR,
        "div.eventlist_asia_fe_EventListLeague_container"
    )

    for league in leagues:
        try:
            name = league.find_element(
                By.CSS_SELECTOR,
                "h3.eventlist_asia_fe_EventListLeague_leagueName span"
            ).text
            if TARGET_LEAGUE_KEYWORD in name:
                return league
        except:
            continue
    return None


def ensure_league_expanded(league):
    events = league.find_elements(
        By.CSS_SELECTOR,
        "div.eventlist_asia_fe_EventListLeague_singleEvent"
    )
    if events:
        return True

    try:
        header = league.find_element(
            By.CSS_SELECTOR,
            "div.eventlist_asia_fe_EventListLeague_headerWrapper"
        )
        header.click()
        time.sleep(1)
        logger.info("🔽 Đã xổ giải E-Soccer Volta")
        return True
    except:
        logger.warning("⚠️ Không xổ được giải")
        return False


def open_event_page_by_player(driver, player_name):
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

        # 👉 SO KHỚP LINH HOẠT
        if not any(player_name.lower() in t.lower() for t in team_names):
            continue

        try:
            time_cell = event.find_element(
                By.CSS_SELECTOR,
                "div.eventlist_asia_fe_sharedGrid_timeCell"
            )

            driver.execute_script(
                "arguments[0].scrollIntoView({block:'center'});",
                time_cell
            )
            time.sleep(0.3)

            logger.info("➡️ Click ô tỉ số để mở trang trận")
            time_cell.click()
            return True

        except:
            logger.warning("⚠️ Không click được ô tỉ số")
            return False

    logger.warning("❌ Không tìm thấy trận chứa người chơi đã nhập")
    return False

def set_stake_amount(driver, amount=50):
    time.sleep(1)  # chờ betslip render

    try:
        stake_input = driver.find_element(
            By.CSS_SELECTOR,
            "input.betslip_fe_CounterSecondary_input"
        )
    except:
        logger.warning("❌ Không tìm thấy ô nhập tiền cược")
        return False

    # ===== ƯU TIÊN CLICK NÚT CLEAR =====
    try:
        clear_btn = driver.find_element(
            By.CSS_SELECTOR,
            "button.betslip_fe_CounterSecondary_counter__clearStakeButton"
        )
        clear_btn.click()
        time.sleep(0.2)
        logger.info("🧹 Đã xóa tiền cược cũ bằng nút clear")
    except:
        # ===== FALLBACK: CTRL+A DELETE =====
        stake_input.click()
        stake_input.send_keys("\u0001")  # Ctrl + A
        stake_input.send_keys("\u0008")  # Backspace
        time.sleep(0.2)
        logger.info("🧹 Đã xóa tiền cược cũ bằng bàn phím")

    # ===== NHẬP TIỀN MỚI =====
    stake_input.send_keys(str(amount))
    logger.info(f"💰 Đã nhập tiền cược: {amount}")
    return True

def place_bet(driver):
    time.sleep(1)

    try:
        place_btn = driver.find_element(By.ID, "place-bets")

        driver.execute_script(
            "arguments[0].scrollIntoView({block:'center'});",
            place_btn
        )

        time.sleep(0.3)

        logger.info("🚀 Nhấn nút Đặt cược")
        place_btn.click()

        return True

    except:
        logger.warning("❌ Không tìm thấy nút Đặt cược")
        return False

def go_back_to_list(driver, wait_time):
    time.sleep(wait_time)

    try:
        back_btn = driver.find_element(
            By.CSS_SELECTOR,
            "div.navigation_eu_fe_Breadcrumbs_link"
        )

        driver.execute_script(
            "arguments[0].scrollIntoView({block:'center'});",
            back_btn
        )
        time.sleep(0.2)

        driver.execute_script("arguments[0].click();", back_btn)

        logger.info("↩️ Đã quay lại danh sách trận")
        return True

    except:
        logger.warning("❌ Không click được nút Trở lại")
        return False
        
def click_team_in_event_page(driver, player_name):
    from selenium.webdriver.support.ui import WebDriverWait
    from selenium.webdriver.support import expected_conditions as EC

    logger.info("🔍 Đang tìm market 1X2...")

    try:
        WebDriverWait(driver, 10).until(
            EC.presence_of_element_located(
                (By.CSS_SELECTOR, "div.eventpage_fe_MoneyLine_markets")
            )
        )
    except:
        logger.warning("❌ Không load được market 1X2")
        return False

    buttons = driver.find_elements(
        By.CSS_SELECTOR,
        "button.eventpage_fe_MoneyLineSelection_line"
    )

    logger.info(f"🔢 Tìm thấy {len(buttons)} lựa chọn")

    for btn in buttons:
        try:
            name_el = btn.find_element(
                By.CSS_SELECTOR,
                "div.eventpage_fe_MoneyLineSelection_meaning"
            )

            name = name_el.get_attribute("title") or name_el.text
            name = name.strip()

            logger.info(f"🔎 So với: {name}")

            if player_name.lower() in name.lower():

                driver.execute_script(
                    "arguments[0].scrollIntoView({block:'center'});",
                    btn
                )
                time.sleep(0.3)

                # ✅ JS CLICK (ổn định hơn click thường)
                driver.execute_script("arguments[0].click();", btn)

                logger.info(f"🟢 ĐÃ CHỌN ĐÚNG ĐỘI: {name}")
                return True

        except Exception as e:
            continue

    logger.warning("❌ KHÔNG TÌM THẤY ĐỘI CẦN CƯỢC")
    return False

# ================= VÒNG LẶP AUTO =================
while True:

    logger.info("🔍 Đang tìm giải E-Soccer Volta...")
    league = None

    while True:
        league = find_volta_league(driver)
        if league:
            logger.info("✅ Đã tìm thấy giải E-Soccer Volta")
            ensure_league_expanded(league)
            break
        time.sleep(2)

    # ================= IN DANH SÁCH =================
    print("\n📋 DANH SÁCH TRẬN & NGƯỜI CHƠI:\n")

    events = league.find_elements(
        By.CSS_SELECTOR,
        "div.eventlist_asia_fe_EventListLeague_singleEvent"
    )

    for idx, event in enumerate(events, 1):
        teams = event.find_elements(
            By.CSS_SELECTOR,
            "span.eventlist_asia_fe_EventCard_teamNameText"
        )
        names = [t.text.strip() for t in teams if t.text.strip()]
        if len(names) >= 2:
            print(f"Trận {idx}: {names[0]}  vs  {names[1]}")

    # ================= NHẬP NGƯỜI CHƠI =================
    target_player = input(
        "\n👉 Nhập TÊN NGƯỜI CHƠI muốn cược (có thể nhập 1 phần): "
    ).strip()

    logger.info(f"🎯 Bạn chọn: {target_player}")

    # ================= MỞ TRẬN =================
    if not open_event_page_by_player(driver, target_player):
        logger.warning("❌ Không mở được trang trận – chờ 10s rồi thử lại")
        time.sleep(10)
        continue

    # ================= CLICK ĐỘI =================
    if click_team_in_event_page(driver, target_player):
        logger.info("✅ ĐÃ CLICK ĐÚNG ĐỘI CẦN CƯỢC")

        set_stake_amount(driver, 50)

        if place_bet(driver):
            logger.info("🎉 CƯỢC THÀNH CÔNG – chờ 5s quay lại")
            go_back_to_list(driver, 5)
        else:
            logger.warning("⚠️ CƯỢC KHÔNG THÀNH CÔNG – chờ 3s quay lại")
            go_back_to_list(driver, 3)

    else:
        logger.warning("❌ KHÔNG CHỌN ĐƯỢC ĐỘI – chờ 3s quay lại")
        go_back_to_list(driver, 3)

    # ================= CHỜ TRƯỚC VÒNG TIẾP =================
    logger.info("⏳ Chờ 10s rồi tiếp tục tìm kèo mới...")
    time.sleep(10)

    # đảm bảo quay về đầu trang (tránh DOM lệch)
    driver.execute_script("window.scrollTo(0, 0);")

# ================= GIỮ CHƯƠNG TRÌNH =================
while True:
    time.sleep(1)
