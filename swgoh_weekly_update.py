import requests
import json
import time
from datetime import datetime
import logging
import os
from logging.handlers import RotatingFileHandler

# Suppress debug/info logs from the requests library
logging.getLogger("requests.packages.urllib3").setLevel(logging.WARNING)
logging.getLogger("urllib3").setLevel(logging.WARNING)

# Ensure necessary directories exist
os.makedirs("logs", exist_ok=True)  # Logs folder directory
os.makedirs("previous_states", exist_ok=True)  # Previous states folder directory

# Timestamp for log file
timestamp = datetime.now().strftime('%Y-%m-%d_%H-%M-%S_%f')[:-3]

# Configure logging with log rotation
log_handler = RotatingFileHandler(
    f"logs/tracker_{timestamp}.log", maxBytes=5 * 1024 * 1024, backupCount=5, encoding="utf-8" 
    # Make sure "logs" matches your logs directory. Replace "tracker" with your desired log file name format.
)
log_handler.setFormatter(logging.Formatter('%(asctime)s - %(message)s', datefmt='%Y-%m-%d %H:%M:%S'))
logging.basicConfig(handlers=[log_handler], level=logging.DEBUG)
logging.getLogger().addHandler(logging.StreamHandler())

# Load configuration from config.json
try:
    with open("config.json", "r") as f:
        config = json.load(f)

    # Set up tracked categories, API config, and accounts from config.json. See the README for configuration guidance.
    tracked_categories = config["filters"]["account"]  # Replace "account" with your account name from config.json.
    api_config = config["api_config"]  # This corresponds to the "api_config" section of config.json.
    accounts = config["accounts"]  # This corresponds to the "accounts" section of config.json.
    categories = config["categories"]  # This contains farming objectives based on rosterUnit.id in config.json.
except (FileNotFoundError, json.JSONDecodeError) as e:
    logging.error(f"Error loading config.json: {e}")
    raise SystemExit("Failed to load configuration.")

# Select the account to use from config.json. See the README for configuration guidance.
account_key = "your_account_key"  # Replace with your actual account key from config.json.
ally_code = accounts[account_key]["allyCode"]  # This corresponds to the ally code in config.json.
discord_webhook_url = accounts[account_key]["DISCORD_WEBHOOK_URL"]  # The Discord webhook URL.
API_URL = api_config["API_URL"]  # The API "player" endpoint from SWGOH Comlink.

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
    """Filter the roster to only include units in the allowed list."""
    return [unit for unit in roster if unit.get("id") in allowed_units]

def format_gear_level(unit):
    """Return a string representing the unit's gear level.
    
    - If relic.currentTier is 0 or 1: use the unit's currentTier as gear level (e.g. "G8").
    - If relic.currentTier is 2: override gear level to "G13".
    - Otherwise (relic.currentTier >= 3): use relic mapping (e.g. "R{relic.currentTier - 2}").
    - If currentTier == 1 and relic.currentTier == 0: return "N/A" (for ships).
    """
    current_tier = unit.get("currentTier", 0)
    relic_tier = unit.get("relic", {}).get("currentTier", 0) if unit.get("relic") else 0

    # Handle ships (currentTier == 1 and relicTier == 0)
    if current_tier == 1 and relic_tier == 0:
        return "N/A"
    
    if relic_tier == 2:
        return "G13"
    elif relic_tier >= 3:
        return f"R{relic_tier - 2}"
    else:
        return f"G{current_tier}"

def format_category_report(roster, category_name):
    """Format and return a progress report for a given category."""
    if not roster:
        return ""

    report_lines = [
        f"\n**__{category_name} Progress__**",  # Asterisks and underscores are for Discord markdown formatting.
        f"{'Name'.ljust(25)} | {'Star Rank'.ljust(12)} | {'Gear Level'.ljust(12)}"
    ]
    for unit in roster:
        name, star_rank = unit["definitionId"].split(":")
        gear_level = format_gear_level(unit)
        report_lines.append(f"{name.ljust(25)} | {star_rank.ljust(12)} | {gear_level.ljust(12)}")
    return "\n".join(report_lines)

def has_update(unit, prev_unit):
    """Check if any key values have changed for a unit."""
    current_star = unit.get("currentRarity", 0)
    current_gear = unit.get("currentTier", 0)
    current_relic = unit.get("relic", {}).get("currentTier", 0) if unit.get("relic") else 0
    prev_star = prev_unit.get("currentRarity", 0)
    prev_gear = prev_unit.get("currentTier", 0)
    prev_relic = prev_unit.get("relicTier", 0)

    # Ignore gear updates for ships
    if current_gear == 1 and current_relic == 0:
        return current_star != prev_star  # Only report star rank changes

    relic_changed = (current_relic >= 2 or prev_relic >= 2) and (current_relic != prev_relic)
    
    return (current_star != prev_star) or (current_gear != prev_gear) or relic_changed

def identify_extra_updates(roster, previous_state):
    """Identify changes for all units and return update messages."""
    updates = []
    for unit in roster:
        unit_id = unit.get("id")
        if not unit_id:
            continue
        
        prev_unit = previous_state.get(unit_id)
        if prev_unit and has_update(unit, prev_unit):
            name = unit["definitionId"].split(":")[0]
            updates.append(f"{name} updated: Star {unit.get('currentRarity')}, Gear G{unit.get('currentTier')}, Relic R{unit.get('relic', {}).get('currentTier', 0)}")
    return updates

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
        for unit in roster if unit.get("id")
    }
    filename = os.path.join(directory, f"previous_state_{time.strftime('%Y-%m-%d_%H-%M-%S')}.json")
    with open(filename, "w") as f:
        json.dump(state_data, f, indent=2)

def send_discord_notification(message):
    """Send a message to Discord webhook."""
    if not discord_webhook_url:
        return 0
    response = requests.post(discord_webhook_url, json={"content": message})
    return response.status_code == 204

def main():
    """Main execution function."""
    data = fetch_player_data()
    if not data:
        return

    previous_state = load_previous_state()
    full_roster = data.get("rosterUnit", [])

    extra_update_messages = identify_extra_updates(full_roster, previous_state)
    
    save_current_state(full_roster)

if __name__ == "__main__":
    main()
