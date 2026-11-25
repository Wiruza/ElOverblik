"""Constants for the Eloverblik integration.

Fork of homeassistant-eloverblik by JonasPed (Apache-2.0), changed for Power Monitor.
"""

from datetime import timedelta

DOMAIN = "eloverblik"

# Config entry keys (refresh_token and metering_point match the original integration,
# so entries made with it keep working).
CONF_REFRESH_TOKEN = "refresh_token"
CONF_METERING_POINT = "metering_point"
CONF_ADDRESS = "address"
CONF_KIND = "kind"
CONF_HISTORY_DAYS = "history_days"

DEFAULT_HISTORY_DAYS = 365
HISTORY_CHOICES = ["30", "90", "365", "730"]

# Eloverblik delivers readings a day or more late; checking every two hours is plenty
# and stays far below the API's limits.
UPDATE_INTERVAL = timedelta(hours=2)

# Re-read this many days before the newest reading: late and corrected readings arrive there.
OVERLAP_DAYS = 3

STORAGE_VERSION = 1

ATTRIBUTION = "Data from Eloverblik.dk"
