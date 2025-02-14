import requests
import json
import time
from datetime import datetime
import logging
import os
from logging.handlers import RotatingFileHandler

# Suppress requests library debug/info logging
logging.getLogger("requests.packages.urllib3").setLevel(logging.WARNING)
logging.getLogger("urllib3").setLevel(logging.WARNING)

# Ensure directories exist
os.makedirs("logs", exist_ok=True)
os.makedirs("previous_states", exist_ok=True)

# Timestamp for log file
timestamp = datetime.now().strftime('%Y-%m-%d_%H-%M-%S_%f')[:-3]

# Configure logging with log rotation
log_handler = RotatingFileHandler(
    f"logs/tracker_{timestamp}.log", maxBytes=5 * 1024 * 1024, backupCount=5, encoding="utf-8"
)
log_handler.setFormatter(logging.Formatter('%(asctime)s - %(message)s', datefmt='%Y-%m-%d %H:%M:%S'))
logging.basicConfig(handlers=[log_handler], level=logging.DEBUG)
logging.getLogger().addHandler(logging.StreamHandler())

# Load configuration
try:
    with open("config.json", "r") as f:
        config = json.load(f)
    tracked_categories = config["filters"]["account"]  # Replace 'account' with your account key in config.json
    api_config = config["api_config"]
    accounts = config["accounts"]
    categories = config["categories"]
except (FileNotFoundError, json.JSONDecodeError) as e:
    logging.error(f"Error loading config.json: {e}")
    raise SystemExit("Failed to load configuration.")

# Set up account details
account_key = "your_account_key"  # Replace with your account key from config.json
ally_code = accounts[account_key]["allyCode"]
discord_webhook_url = accounts[account_key]["DISCORD_WEBHOOK_URL"]
API_URL = api_config["API_URL"]

# Relic tier mapping (if needed)
RELIC_TIER_MAP = {i: f"R{i-2}" for i in range(3, 53)}
RELIC_TIER_MAP.update({0: "N/A", 1: "Locked", 2: "Unlocked"})

def fetch_player_data():
    """Fetch player data from the API."""
    try:
        response = requests.post(API_URL, json={"payload": {"allyCode": ally_code}, "enums": False}, timeout=10)
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        logging.exception("Error fetching player profile")
        return None

def filter_roster(roster, allowed_units):
    """Return only units whose 'id' is in the allowed_units list."""
    return [unit for unit in roster if unit.get("id") in allowed_units]

def format_gear_level(unit):
    """Return a formatted gear level string based on relic and tier values."""
    current_tier = unit.get("currentTier", 0)
    relic_tier = unit.get("relic", {}).get("currentTier", 0) if unit.get("relic") else 0
    if relic_tier == 2:
        return "G13"
    elif relic_tier >= 3:
        return f"R{relic_tier - 2}"
    else:
        return f"G{current_tier}"

def format_category_report(roster, category_name):
    """Format a report for a specific category."""
    if not roster:
        return ""
    report_lines = [
        f"\n{category_name} Progress",
        f"{'Name'.ljust(25)} | {'Star Rank'.ljust(12)} | {'Gear Level'.ljust(12)}",
        "-" * 52
    ]
    for unit in roster:
        name, star_rank = unit["definitionId"].split(":")
        gear_level = format_gear_level(unit)
        report_lines.append(f"{name.ljust(25)} | {star_rank.ljust(12)} | {gear_level.ljust(12)}")
    report_lines.append("\n" + "=" * 80 + "\n")
    return "\n".join(report_lines)

def load_previous_state(directory="previous_states"):
    """Load the most recent previous state file."""
    try:
        files = [f for f in os.listdir(directory) if f.startswith("previous_state_")]
        if not files:
            return {}
        latest_file = max(files, key=lambda f: os.path.getmtime(os.path.join(directory, f)))
        with open(os.path.join(directory, latest_file), "r") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}

def save_current_state(roster, directory="previous_states"):
    """Save the current state of the roster."""
    state_data = {
        unit.get("id"): {
            "id": unit.get("id"),
            "definitionId": unit.get("definitionId"),
            "currentRarity": unit.get("currentRarity"),
            "currentTier": unit.get("currentTier"),
            "relicTier": unit.get("relic", {}).get("currentTier", 0) if unit.get("relic") else 0
        }
        for unit in roster
    }
    filename = os.path.join(directory, f"previous_state_{time.strftime('%Y-%m-%d_%H-%M-%S')}.json")
    with open(filename, "w") as f:
        json.dump(state_data, f, indent=2)

def send_discord_notification(message):
    """Send a message to Discord via webhook."""
    if not discord_webhook_url:
        return 0
    max_length = 1950
    chunks = [message[i:i + max_length] for i in range(0, len(message), max_length)]
    sent_count = 0
    for chunk in chunks:
        try:
            response = requests.post(discord_webhook_url, json={"content": chunk})
            response.raise_for_status()
            sent_count += 1
        except requests.exceptions.RequestException as e:
            logging.error(f"Error sending Discord message: {e}")
    return sent_count

def main():
    """Main execution function."""
    data = fetch_player_data()
    if not data:
        return

    previous_state = load_previous_state()
    full_roster = data.get("rosterUnit", [])
    
    tracked_units = set()
    for category_name in tracked_categories:
        if category_name in categories:
            tracked_units.update(categories[category_name])
    
    all_category_reports = []
    for category_name in tracked_categories:
        if category_name in categories:
            category_roster = filter_roster(full_roster, categories[category_name])
            report = format_category_report(category_roster, category_name)
            if report:
                all_category_reports.append(report)
    
    full_report = "\n".join(all_category_reports).strip()
    if full_report:
        logging.info("\n" + full_report)
        send_discord_notification(full_report)
    
    save_current_state(full_roster)

if __name__ == "__main__":
    main()
