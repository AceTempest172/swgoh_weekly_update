"""
Generic SWGOH Account Tracker
- Reads config.json for accounts, filters, categories
- Compares current roster to previously saved state
- Sends tracked and untracked updates to Discord (with smart chunk splitting)
"""

import requests
import json
import time
from datetime import datetime
import logging
import os
from logging.handlers import RotatingFileHandler

# ------------------------------
# CONFIGURE THESE DIRECTORIES PER SCRIPT INSTANCE
# ------------------------------
LOG_DIR = "main_logs"  # <-- Change for alt accounts (e.g., "alt_logs")
STATE_DIR = "previous_states_main"  # <-- Change for alt accounts (e.g., "previous_states_alt")

# ------------------------------
# CONFIGURE ACCOUNT KEY PER SCRIPT INSTANCE
# ------------------------------
ACCOUNT_KEY = "account1"  # <-- Must match a key in config.json["accounts"]

# ------------------------------
# SUPPRESS VERBOSE LOGGING
# ------------------------------
logging.getLogger("requests.packages.urllib3").setLevel(logging.WARNING)
logging.getLogger("urllib3").setLevel(logging.WARNING)

# Ensure directories exist
os.makedirs(LOG_DIR, exist_ok=True)
os.makedirs(STATE_DIR, exist_ok=True)

# Timestamp for log file
timestamp = datetime.now().strftime('%Y-%m-%d_%H-%M-%S_%f')[:-3]

# Configure logging with rotation
log_handler = RotatingFileHandler(
    f"{LOG_DIR}/{ACCOUNT_KEY}_{timestamp}.log",
    maxBytes=5 * 1024 * 1024,
    backupCount=5,
    encoding="utf-8"
)
log_handler.setFormatter(logging.Formatter('%(asctime)s - %(message)s', datefmt='%Y-%m-%d %H:%M:%S'))
logging.basicConfig(handlers=[log_handler], level=logging.DEBUG)
logging.getLogger().addHandler(logging.StreamHandler())

# ------------------------------
# LOAD CONFIG FILE
# ------------------------------
try:
    with open("config.json", "r") as f:
        config = json.load(f)

    # Filters: determines which categories this account tracks
    tracked_categories = config["filters"][ACCOUNT_KEY]  # <-- Must match config.json key in "filters"

    api_config = config["api_config"]
    accounts = config["accounts"]
    categories = config["categories"]

except (FileNotFoundError, json.JSONDecodeError) as e:
    logging.error(f"Error loading config.json: {e}")
    raise SystemExit("Failed to load configuration.")

# ------------------------------
# ACCOUNT-SPECIFIC SETTINGS
# ------------------------------
ally_code = accounts[ACCOUNT_KEY]["allyCode"]
discord_webhook_url = accounts[ACCOUNT_KEY]["DISCORD_WEBHOOK_URL"]
API_URL = api_config["API_URL"]

# ------------------------------
# RELIC TIER MAPPING
# ------------------------------
RELIC_TIER_MAP = {i: f"R{i-2}" for i in range(3, 53)}
RELIC_TIER_MAP.update({0: "N/A", 1: "Locked", 2: "Unlocked"})

# ------------------------------
# DATA FETCH
# ------------------------------
def fetch_player_data():
    """Fetch player data from the API."""
    try:
        response = requests.post(API_URL, json={"payload": {"allyCode": ally_code}, "enums": False}, timeout=10)
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException:
        logging.exception("Error fetching player profile")
        return None

# ------------------------------
# DATA FILTERS
# ------------------------------
def filter_roster(roster, allowed_units):
    """Return only units whose 'id' is in the allowed_units list."""
    return [unit for unit in roster if unit.get("id") in allowed_units]

def format_gear_level(unit):
    """Return gear/relic string with ship handling."""
    current_tier = unit.get("currentTier", 0)
    relic_tier = (unit.get("relic") or {}).get("currentTier", 0)

    if current_tier == 1 and relic_tier == 0:
        return "N/A"  # Ships
    if relic_tier == 2:
        return "G13"
    elif relic_tier >= 3:
        return f"R{relic_tier - 2}"
    else:
        return f"G{current_tier}"

def format_category_report(roster, category_name):
    """Format a report for a given category."""
    if not roster:
        return ""
    lines = [
        f"\n**__{category_name} Progress__**",
        f"{'Name'.ljust(25)} | {'Star Rank'.ljust(12)} | {'Gear Level'.ljust(12)}",
    ]
    for unit in roster:
        name, star = unit["definitionId"].split(":")
        lines.append(f"{name.ljust(25)} | {star.ljust(12)} | {format_gear_level(unit).ljust(12)}")
    return "\n".join(lines)

# ------------------------------
# CHANGE DETECTION
# ------------------------------
def has_update(unit, prev_unit):
    """Return True if any tracked values have changed."""
    current_star = unit.get("currentRarity", 0)
    current_gear = unit.get("currentTier", 0)
    current_relic = (unit.get("relic") or {}).get("currentTier", 0)

    prev_star = prev_unit.get("currentRarity", 0)
    prev_gear = prev_unit.get("currentTier", 0)
    prev_relic = prev_unit.get("relicTier", 0)

    if current_gear == 1 and current_relic == 0:  # Ships
        return current_star != prev_star

    relic_changed = (current_relic >= 2 or prev_relic >= 2) and (current_relic != prev_relic)
    return current_star != prev_star or current_gear != prev_gear or relic_changed

def identify_nontracked_updates(roster, previous_state, tracked_units):
    """List of updates for non-tracked units."""
    msgs = []
    for u in roster:
        uid = u.get("id")
        if uid in tracked_units:
            continue
        prev = previous_state.get(uid, {})
        if not has_update(u, prev):
            continue

        name, _ = u["definitionId"].split(":")
        cs, cg, cr = u.get("currentRarity", 0), u.get("currentTier", 0), (u.get("relic") or {}).get("currentTier", 0)
        ps, pg, pr = prev.get("currentRarity", 0), prev.get("currentTier", 0), prev.get("relicTier", 0)

        if cg == 1 and cr == 0:  # Ships
            if ps != cs:
                msgs.append(f"{name} promoted from {ps} star to {cs} star.")
            continue

        if ps != cs:
            msgs.append(f"{name} promoted from {ps} star to {cs} star.")

        direct_relic = (pg == 12 and pr == 1 and cr >= 2)
        if pg != cg and not direct_relic:
            msgs.append(f"{name} upgraded gear from G{pg} to G{cg}.")

        if cr >= 2 and pr != cr:
            pr_str = f"G{pg}" if pr == 1 else f"R{pr - 2}" if pr >= 2 else "Unknown"
            cr_str = f"R{cr - 2}"
            msgs.append(f"{name} upgraded from {pr_str} to {cr_str}.")

    return msgs

# ------------------------------
# STATE SAVE/LOAD
# ------------------------------
def load_previous_state():
    """Load latest saved state."""
    try:
        files = [f for f in os.listdir(STATE_DIR) if f.startswith(f"previous_state_{ACCOUNT_KEY}_")]
        if not files:
            return {}
        latest = max(files, key=lambda f: os.path.getmtime(os.path.join(STATE_DIR, f)))
        return json.load(open(os.path.join(STATE_DIR, latest)))
    except:
        return {}

def save_current_state(roster):
    """Save current state for next comparison."""
    st = {
        u["id"]: {
            "currentRarity": u.get("currentRarity", 0),
            "currentTier": u.get("currentTier", 0),
            "relicTier": (u.get("relic") or {}).get("currentTier", 0)
        }
        for u in roster
    }
    fn = os.path.join(STATE_DIR, f"previous_state_{ACCOUNT_KEY}_{time.strftime('%Y-%m-%d_%H-%M-%S')}.json")
    json.dump(st, open(fn, "w"), indent=2)

# ------------------------------
# DISCORD OUTPUT
# ------------------------------
def send_discord_notification(message):
    """Send to Discord, moving last line of each chunk into the next."""
    if not discord_webhook_url:
        return 0

    max_len = 1950
    lines = message.split("\n")
    chunks, cur, cur_len = [], [], 0

    for line in lines:
        l_len = len(line) + 1
        if cur_len + l_len > max_len:
            last = cur.pop() if cur else ""
            chunks.append("\n".join(cur))
            cur = [last, line] if last else [line]
            cur_len = sum(len(x) + 1 for x in cur)
        else:
            cur.append(line)
            cur_len += l_len

    if cur:
        chunks.append("\n".join(cur))

    sent = 0
    for c in chunks:
        try:
            r = requests.post(discord_webhook_url, json={"content": c})
            r.raise_for_status()
            sent += 1
        except Exception as e:
            logging.error(f"Error sending Discord message: {e}")
    return sent

# ------------------------------
# MAIN EXECUTION
# ------------------------------
def main():
    data = fetch_player_data()
    if not data:
        return

    prev = load_previous_state()
    roster = data.get("rosterUnit", [])

    tracked = {uid for cat in tracked_categories for uid in categories.get(cat, [])}

    reports = []
    for cat in tracked_categories:
        part = filter_roster(roster, categories.get(cat, []))
        rep = format_category_report(part, cat)
        if rep:
            reports.append(rep)

    extra = identify_nontracked_updates(roster, prev, tracked)
    reports.append("\nExtra Updates:")
    reports.append("\n".join(extra) if extra else "No extra updates.")

    full = "\n".join(reports).strip()
    if full:
        logging.info("\n" + full)
        sent = send_discord_notification(full)
        logging.info(f"Messages sent to Discord: {sent}")

    save_current_state(roster)

if __name__ == "__main__":
    main()
