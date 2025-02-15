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
os.makedirs("logs", exist_ok=True) # Logs folder directory
os.makedirs("previous_states", exist_ok=True) # Previous states folder directory

# Timestamp for log file
timestamp = datetime.now().strftime('%Y-%m-%d_%H-%M-%S_%f')[:-3]

# Configure logging with log rotation
log_handler = RotatingFileHandler(
    f"logs/tracker_{timestamp}.log", maxBytes=5 * 1024 * 1024, backupCount=5, encoding="utf-8" # Make sure to match this to your logs and previous states folders directories. Replace "tracker" with the desired log file name format.
)
log_handler.setFormatter(logging.Formatter('%(asctime)s - %(message)s', datefmt='%Y-%m-%d %H:%M:%S'))
logging.basicConfig(handlers=[log_handler], level=logging.DEBUG)
logging.getLogger().addHandler(logging.StreamHandler())

# Load configuration from config.json
try:
    with open("config.json", "r") as f:
        config = json.load(f)

    # Set up tracked categories, API config, and accounts from config.json. See the README for configuration guidance.
    tracked_categories = config["filters"]["account"]  # Replace "account" with your account name from the config.json file. The categories containing the rosterUnit information will be listed here as part of configuration.
    api_config = config["api_config"] # This corresponds to the "api_config" section of the config.json file
    accounts = config["accounts"] # This corresponds to the "accounts" section of the config.json file
    categories = config["categories"] # This contains your farming objectives within each category based on the rosterUnit.id field. These categories are specified in the "filters" section of the config.json file
except (FileNotFoundError, json.JSONDecodeError) as e:
    logging.error(f"Error loading config.json: {e}")
    raise SystemExit("Failed to load configuration.")

# Select the account to use from config.json. See the README for configuration guidance.
account_key = "your_account_key"  # Replace with your actual account name from the config.json file.
ally_code = accounts[account_key]["allyCode"] # Accounts corresponds to the "Accounts" section of the config.json file. "allyCode" corresponds to the ally code value contained within each account.
discord_webhook_url = accounts[account_key]["DISCORD_WEBHOOK_URL"] # The discord_webhook_url is similarly contained within the individual account in the "Accounts" section.
API_URL = api_config["API_URL"] # This contains the API "player" endpoint from the SWGOH comlink API.

# Relic tier mapping
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
        f"\n**__{category_name} Progress__**", # Asteriks and underscores are for Discord markdown formatting, remove if undesired
        f"{'Name'.ljust(25)} | {'Star Rank'.ljust(12)} | {'Gear Level'.ljust(12)}",
        # "-" * 52 # Adds separation between header and objectives when uncommented out
    ]
    for unit in roster:
        name, star_rank = unit["definitionId"].split(":")
        gear_level = format_gear_level(unit)
        report_lines.append(f"{name.ljust(25)} | {star_rank.ljust(12)} | {gear_level.ljust(12)}")
    # report_lines.append("\n" + "=" * 80 + "\n") # Extra separation between categories in final report when uncommented out
    return "\n".join(report_lines)

def has_update(unit, prev_unit):
    """
    Return True if any key values have changed between unit and prev_unit.
    For relic, only consider changes if at least one of the values is 2 or higher.
    Exclude ships (currentTier == 1 and relicTier == 0) from extra updates.
    """
    current_star = unit.get("currentRarity", 0)
    current_gear = unit.get("currentTier", 0)
    current_relic = unit.get("relic", {}).get("currentTier", 0) if unit.get("relic") else 0
    prev_star = prev_unit.get("currentRarity", 0)
    prev_gear = prev_unit.get("currentTier", 0)
    prev_relic = prev_unit.get("relicTier", 0)

    # Exclude ships from triggering updates in extra updates
    if current_gear == 1 and current_relic == 0:
        return current_star != prev_star  # Only report if star rank changes

    relic_changed = False
    if current_relic >= 2 or prev_relic >= 2:
        relic_changed = (current_relic != prev_relic)
    
    return (current_star != prev_star) or (current_gear != prev_gear) or relic_changed

def identify_extra_updates(roster, previous_state):
    """Identify changes for all units."""
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

def load_previous_state(directory="previous_states"): # Make sure "previous_states" matches your previous states directory set at the start of this script
    """Load the most recent previous state file."""
    try:
        files = [f for f in os.listdir(directory) if f.startswith("previous_state_")] # Make sure "previous_states" matches your previous states directory set at the start of this script. The extra "_" has the timestamp following it.
        if not files:
            return {}
        latest_file = max(files, key=lambda f: os.path.getmtime(os.path.join(directory, f)))
        with open(os.path.join(directory, latest_file), "r") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}

def save_current_state(roster, directory="previous_states"): # Make sure "previous_states" matches your previous states directory set at the start of this script
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
    filename = os.path.join(directory, f"previous_state_{time.strftime('%Y-%m-%d_%H-%M-%S')}.json") # Make sure "previous_states" matches your previous states directory set at the start of this script. The extra "_" has the timestamp following it.
    with open(filename, "w") as f:
        json.dump(state_data, f, indent=2)
    logging.info(f"Saved current state to {filename}")

def send_discord_notification(message):
    """Send a message to Discord webhook."""
    if not discord_webhook_url:
        logging.warning("Discord webhook URL is not set.")
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
    for category_name in config["filters"]["account"]: # Replace "filters" and the contained "account" with the appropriate names from the config.json file
        if category_name in categories:
            tracked_units.update(categories[category_name])
    
    all_category_reports = []
    for category_name in tracked_categories:
        if category_name in categories:
            category_roster = filter_roster(full_roster, categories[category_name])
            report = format_category_report(category_roster, category_name)
            if report:
                all_category_reports.append(report)
    
    extra_update_messages = identify_extra_updates(full_roster, previous_state)
    all_category_reports.append("\nExtra Updates:")
    if extra_update_messages:
        all_category_reports.append("\n".join(extra_update_messages))
    else:
        all_category_reports.append("No extra updates.")

    full_report = "\n".join(all_category_reports).strip()
    messages_sent = send_discord_notification(full_report)

    logging.info(f"\n{full_report}")
    logging.info(f"Messages sent to Discord: {messages_sent}")

    save_current_state(full_roster)

if __name__ == "__main__":
    main()
