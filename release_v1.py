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

# ================= LOGGER =================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)
logger = logging.getLogger(__name__)

# ================= CONFIG =================
WEB_URL = "https://prod20091.fxf774.com/vi/asian-view/live/B%C3%B3ng-%C4%91%C3%A1?operatorToken=43-be38e386ed5bcfa2f3bbec8c4d2fea1f"

API_TOKEN = "247066-ZFfFhtCGjGEUhw"

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
    if "(" in full and ")" in full:
        return full.split("(")[1].rstrip(")").strip()
    return full.strip()

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
        r = requests.get(
            f"{B365_API_BASE}/events/ended{params}",
            timeout=15
        ).json()
        return r.get("results", [])
    except:
        return []

def initialize_historical_data():
    end = datetime.now()
    start = end - timedelta(days=FORM_DAYS)
    current = start

    while current <= end:
        day_str = current.strftime("%Y%m%d")
        for m in fetch_finished_matches(day_str):
            process_match(m)
        current += timedelta(days=1)
        time.sleep(0.6)

def process_match(m):
    if not m.get("ss"):
        return

    try:
        hg, ag = map(int, m["ss"].split("-"))
    except:
        return

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
        r = requests.get(
            f"{BETSAPI_BASE}/events/inplay",
            params={
                "token": API_TOKEN,
                "sport_id": SPORT_ID
            },
            timeout=10
        ).json()
        return r.get("results", [])
    except:
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

def ensure_league_expanded(league):
    if not league.find_elements(
        By.CSS_SELECTOR,
        "div.eventlist_asia_fe_EventListLeague_singleEvent"
    ):
        league.click()
        time.sleep(1)

def open_event_page_by_player(player, retry=3):
    for _ in range(retry):
        try:
            events = driver.find_elements(
                By.CSS_SELECTOR,
                "div.eventlist_asia_fe_EventListLeague_singleEvent"
            )
            for e in events:
                teams = e.find_elements(
                    By.CSS_SELECTOR,
                    "span.eventlist_asia_fe_EventCard_teamNameText"
                )
                names = [t.text.strip() for t in teams if t.text.strip()]
                if not any(player.lower() in n.lower() for n in names):
                    continue

                time_cell = e.find_element(
                    By.CSS_SELECTOR,
                    "div.eventlist_asia_fe_sharedGrid_timeCell"
                )
                driver.execute_script(
                    "arguments[0].scrollIntoView({block:'center'});",
                    time_cell
                )
                time.sleep(0.3)
                driver.execute_script("arguments[0].click();", time_cell)
                return True
        except StaleElementReferenceException:
            time.sleep(0.5)
    return False

def click_team(player):
    WebDriverWait(driver, 10).until(
        EC.presence_of_element_located(
            (By.CSS_SELECTOR, "button.eventpage_fe_MoneyLineSelection_line")
        )
    )
    for b in driver.find_elements(
        By.CSS_SELECTOR,
        "button.eventpage_fe_MoneyLineSelection_line"
    ):
        if player.lower() in b.text.lower():
            b.click()
            return True
    return False

def set_stake(amount):
    inp = driver.find_element(
        By.CSS_SELECTOR,
        "input.betslip_fe_CounterSecondary_input"
    )
    inp.clear()
    inp.send_keys(str(amount))

def place_bet():
    driver.find_element(By.ID, "place-bets").click()

def go_back():
    time.sleep(3)
    driver.back()
    time.sleep(2)

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
        time.sleep(15)
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