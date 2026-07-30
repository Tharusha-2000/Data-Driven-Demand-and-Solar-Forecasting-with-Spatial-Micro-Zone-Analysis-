"""
weather_sync.py
───────────────
Fetches 7-day hourly weather forecast from Open-Meteo for Nugegoda.
Run manually:  python3 weather_sync.py
Runs nightly:  scheduled automatically inside app.py at 01:00 Asia/Colombo
"""

import requests
import pandas as pd
import json
from datetime import datetime, timedelta
from pathlib import Path

# ── Configuration ────────────────────────────────────────────
LATITUDE   = 6.8699688
LONGITUDE  = 79.8882835
TIMEZONE   = "Asia/Colombo"

# Save next to this script (inside /dashboard/)
BASE_DIR       = Path(__file__).parent
OUTPUT_JSON    = BASE_DIR / "weather_next_7_days.json"
OUTPUT_RAW_JSON = BASE_DIR / "weather_next_7_days_raw.json"


def fetch_weather() -> dict:
    """
    Download 7-day hourly weather data from Open-Meteo and save to JSON.
    Returns a summary dict for logging.
    """
    today    = datetime.now().date()
    end_date = today + timedelta(days=6)

    url = (
        "https://api.open-meteo.com/v1/forecast"
        f"?latitude={LATITUDE}"
        f"&longitude={LONGITUDE}"
        "&hourly="
        "temperature_2m,"
        "cloudcover,"
        "shortwave_radiation,"
        "precipitation,"
        "wind_speed_10m,"
        "wind_direction_10m"
        f"&start_date={today}"
        f"&end_date={end_date}"
        f"&timezone={TIMEZONE}"
    )

    print(f"[weather_sync] Fetching weather data ({today} → {end_date}) …")

    response = requests.get(url, timeout=30)
    response.raise_for_status()
    data = response.json()

    # ── Save raw API response ────────────────────────────────
    with open(OUTPUT_RAW_JSON, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

    # ── Build clean DataFrame ────────────────────────────────
    df = pd.DataFrame(data["hourly"])
    df.rename(columns={
        "time":              "Timestamp",
        "temperature_2m":    "Temperature (°C)",
        "cloudcover":        "Cloud Cover (%)",
        "shortwave_radiation": "Solar Radiation (W/m²)",
        "precipitation":     "Precipitation (mm)",
        "wind_speed_10m":    "Wind Speed (m/s)",
        "wind_direction_10m": "Wind Direction (°)"
    }, inplace=True)

    df["Timestamp"] = pd.to_datetime(df["Timestamp"])
    df["Date"]      = df["Timestamp"].dt.strftime("%Y-%m-%d")
    df["Time"]      = df["Timestamp"].dt.strftime("%H:%M")

    df = df[[
        "Date", "Time",
        "Temperature (°C)", "Cloud Cover (%)",
        "Solar Radiation (W/m²)", "Precipitation (mm)",
        "Wind Speed (m/s)", "Wind Direction (°)"
    ]]

    records = df.to_dict(orient="records")

    with open(OUTPUT_JSON, "w", encoding="utf-8") as f:
        json.dump(records, f, indent=2, ensure_ascii=False)

    summary = {
        "status":   "success",
        "synced_at": datetime.now().isoformat(),
        "records":  len(records),
        "date_from": str(today),
        "date_to":   str(end_date),
    }
    print(f"[weather_sync] ✅ {len(records)} hourly records saved → {OUTPUT_JSON}")
    return summary


# ── Standalone run ────────────────────────────────────────────
if __name__ == "__main__":
    result = fetch_weather()
    print("Done.", result)
