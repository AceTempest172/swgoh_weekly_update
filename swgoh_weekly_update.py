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

# Ensure necessary directories exist
os.makedirs("logs", exist_ok=True)  # Replace "logs" with your preferred logs directory name
os.makedirs("previous_states", exist_ok=True)  # Replace "previous_states" with your preferred directory for tracking previous states

# Timestamp for log file
timestamp = datetime.now().strftime('%Y-%m-%d_%H-%M-%S_%f')[:-3]

# Configure logging with log rotation
log_handler = RotatingFileHandler(
    f"logs/tracker_{timestamp}.log", maxBytes=5 * 1024 * 1024, backupCount=5, encoding="utf-8"
)  # Ensure this matches your logs directory
log_handler.setFormatter(logging.Formatter('%(asctime)s - %(message)s', datefmt='%Y-%m-%d %H:%M:%S'))
logging.basicConfig(handlers=[log_handler], level=logging.DEBUG)
logging.getLogger().addHandler(logging.StreamHandler())

# Load configuration from config.json
try:
    with open("config.json", "r") as f:
        config = json.load(f)

    # Load necessary settings from config.json. See README for setup guidance.
    tracked_categories = config["filters"]["account_name"]  # Replace "account_name" with the appropriate account key in your config file
    api_config = config["api_config"]  # This should contain API configuration details
    accounts = config["accounts"]  # This should list all accounts being tracked
    categories = config["categories"]  # This contains unit farming objectives grouped by category
except (FileNotFoundError, json.JSONDecodeError) as e:
    logging.error(f"Error loading config.json: {e}")
    raise SystemExit("Failed to load configuration.")

# Select the account to use. Replace "account_key" with your actual account key from config.json.
account_key = "your_account_key"  # Change this to match your account in the config file
ally_code = accounts[account_key]["allyCode"]  # Ally code for the selected account
discord_webhook_url = accounts[account_key]["DISCORD_WEBHOOK_URL"]  # Webhook for Discord notifications
API_URL = api_config["API_URL"]  # API endpoint for fetching player data

# Relic tier mapping for readable output
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
    """Return a string representing the unit's gear or relic level.
    
    - If relic.currentTier is 0 or 1: use currentTier as gear level (e.g., "G8").
    - If relic.currentTier is 2: override gear level to "G13".
    - Otherwise (relic.currentTier >= 3): use relic mapping (e.g., "R{relic.currentTier - 2}").
    - If currentTier == 1 and relic.currentTier == 0: return "N/A" (for ships).
    """
    current_tier = unit.get("currentTier", 0)
    relic_tier = unit.get("relic", {}).get("currentTier", 0) if unit.get("relic") else 0

    if current_tier == 1 and relic_tier == 0:
        return "N/A"  # Ships do not use gear levels
    
    if relic_tier == 2:
        return "G13"
    elif relic_tier >= 3:
        return f"R{relic_tier - 2}"
    else:
        return f"G{current_tier}"

def format_category_report(roster, category_name):
    """Format a report for a given category using the full roster provided."""
    if not roster:
        return ""
    
    report_lines = [
        f"\n**__{category_name} Progress__**",  # Discord Markdown Formatting
        f"{'Name'.ljust(25)} | {'Star Rank'.ljust(12)} | {'Gear Level'.ljust(12)}",
    ]
    for unit in roster:
        name, star_rank = unit["definitionId"].split(":")
        gear_level = format_gear_level(unit)
        report_lines.append(f"{name.ljust(25)} | {star_rank.ljust(12)} | {gear_level.ljust(12)}")
    return "\n".join(report_lines)

def has_update(unit, prev_unit):
    """Check if a unit has changed since the last recorded state."""
    current_star = unit.get("currentRarity", 0)
    current_gear = unit.get("currentTier", 0)
    current_relic = unit.get("relic", {}).get("currentTier", 0) if unit.get("relic") else 0
    prev_star = prev_unit.get("currentRarity", 0)
    prev_gear = prev_unit.get("currentTier", 0)
    prev_relic = prev_unit.get("relicTier", 0)

    # Ignore gear updates for ships
    if current_gear == 1 and current_relic == 0:
        return current_star != prev_star  # Only report star promotions

    relic_changed = (current_relic >= 2 or prev_relic >= 2) and (current_relic != prev_relic)
    return current_star != prev_star or current_gear != prev_gear or relic_changed

def identify_nontracked_updates(roster, previous_state, tracked_units):
    """Return a list of update messages for units not in tracked_units that have changed."""
    messages = []
    for unit in roster:
        unit_id = unit.get("id")
        if unit_id in tracked_units:
            continue  # Only process non-tracked units

        prev_unit = previous_state.get(unit_id, {})
        if has_update(unit, prev_unit):
            name, _ = unit["definitionId"].split(":")
            current_star = unit.get("currentRarity", 0)
            current_gear = unit.get("currentTier", 0)
            current_relic = unit.get("relic", {}).get("currentTier", 0) if unit.get("relic") else 0

            prev_star = prev_unit.get("currentRarity", 0)
            prev_gear = prev_unit.get("currentTier", 0)
            prev_relic = prev_unit.get("relicTier", 0)

            # If this is a ship (currentTier == 1 and relicTier == 0), ignore gear updates
            if current_gear == 1 and current_relic == 0:
                if prev_star != current_star:
                    messages.append(f"{name} promoted from {prev_star} star to {current_star} star.")
                continue

            # Regular unit updates
            if prev_star != current_star:
                messages.append(f"{name} promoted from {prev_star} star to {current_star} star.")
            # ... (gear & relic “from→to” logic here) ...
    return messages

def send_discord_notification(message):
    """Send to Discord, moving the last line of each chunk into the next chunk."""
    if not discord_webhook_url:
        return 0

    max_length = 1950
    lines = message.split("\n")
    chunks = []
    current_chunk = []
    current_len = 0

    for line in lines:
        line_len = len(line) + 1  # account for newline
        if current_len + line_len > max_length:
            # pop the last line off
            last = current_chunk.pop() if current_chunk else ""
            # close out this chunk
            chunks.append("\n".join(current_chunk))
            # start next chunk with that last line plus current
            current_chunk = [last, line] if last else [line]
            current_len = sum(len(l) + 1 for l in current_chunk)
        else:
            current_chunk.append(line)
            current_len += line_len

    if current_chunk:
        chunks.append("\n".join(current_chunk))

    sent = 0
    for chunk in chunks:
        try:
            resp = requests.post(discord_webhook_url, json={"content": chunk})
            resp.raise_for_status()
            sent += 1
        except requests.exceptions.RequestException as e:
            logging.error(f"Error sending Discord message: {e}")
    return sent

def main():
    """Main execution function."""
    data = fetch_player_data()
    if not data:
        return

    # --- load your previous_state here ---
    previous_state = {}  

    full_roster = data.get("rosterUnit", [])
    tracked = set()  # fill from config similarly to above
    # generate your category reports and extra updates...
    extra_update_messages = identify_nontracked_updates(full_roster, previous_state, tracked)
    full_report = "\n".join(extra_update_messages).strip()

    if full_report:
        logging.info("\n" + full_report)
        sent = send_discord_notification(full_report)  # <-- now uses the new “move‐line” logic
        logging.info(f"Messages sent to Discord: {sent}")

if __name__ == "__main__":
    main()
