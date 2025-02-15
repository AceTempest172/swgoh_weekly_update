# Star Wars: Galaxy of Heroes Weekly Updates

This script tracks unit progress in Star Wars: Galaxy of Heroes (SWGOH) by fetching player data from the SWGOH comlink API, comparing changes over time, and sending updates to a configured Discord webhook.

## Features
- Tracks specified units based on configuration.
- Detects upgrades in stars, gear, and relic levels.
- Saves and compares previous states for change detection.
- Sends update notifications to a Discord webhook.
- Supports multiple accounts with separate tracking configurations.

## Requirements
- Python 3.7+
- `requests` library (install via `pip install requests`)

## Setup
1. Clone this repository:
   ```sh
   git clone <repository_url>
   cd <repository_name>
   ```
2. Install required dependencies:
   ```sh
   pip install -r requirements.txt
   ```
3. Create a `config.json` file in the root directory and configure it as described below.
4. Run the script:
   ```sh
   swgoh_weekly_update.py
   ```

## Configuration
Create a `config.json` file in the root directory with the following structure:

```json
{
    // Filters for accounts: list the category names for each account.
    "filters": {
        "account_1": [
            // Example: "category_1", "category_2"
        ],
        "account_2": [
            // Example: "category_1", "category_2"
        ]
    },
    // Categories: each key is a category name and its value is a list of unit IDs. You can find these IDs by calling the "player" endpoint of the SWGOH comlink API
    "categories": {
        "category_1": [
            // Add unit IDs for category_1 here.
        ],
        "category_2": [
            // Add unit IDs for category_2 here.
        ]
    },
    // Account details: configuration for each account.
    "accounts": {
        "account_1": {
            "allyCode": "",               // Enter the ally code for account_1.
            "DISCORD_WEBHOOK_URL": ""     // Enter the Discord webhook URL for account_1.
        },
        "account_2": {
            "allyCode": "",               // Enter the ally code for account_2.
            "DISCORD_WEBHOOK_URL": ""     // Enter the Discord webhook URL for account_2.
        }
    },
    // API configuration: specify the API URL.
    "api_config": {
        "API_URL": ""                   // The base URL for the API. This is intended to function with the SWGOH comlink API, specifically the "player" endpoint
    }
}
```

### Explanation
- **filters**: Defines categories of units to track for each account.
- **categories**: Groups units by category, allowing easy filtering.
- **accounts**: Stores player-specific details, including `allyCode` and Discord webhook URL.
- **api_config**: Stores the base API URL used to fetch player data.

## Logging
- Logs are stored in the `logs/` directory and automatically rotate.
- Previous state files are stored in `previous_states/` for tracking progress over time.

## License
This project is currently unlicensed and therefore not open-source.

