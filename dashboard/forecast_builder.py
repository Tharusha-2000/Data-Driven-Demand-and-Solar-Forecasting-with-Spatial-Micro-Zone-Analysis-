"""
forecast_builder.py
────────────────────
Combines weather_next_7_days.json + calendar_2026.json into a
Prophet-ready feature matrix and saves it as forecast_input_7days.json.

Runs automatically every night at 01:02 Asia/Colombo (2 min after
the weather sync at 01:00 finishes writing fresh data).

Standalone:
    python3 forecast_builder.py
"""

import json
import pandas as pd
from pathlib import Path
from datetime import datetime

# ── File paths ───────────────────────────────────────────────
BASE_DIR         = Path(__file__).parent
WEATHER_JSON     = BASE_DIR / "weather_next_7_days.json"
CALENDAR_JSON    = BASE_DIR / "calendar_2026.json"
OUTPUT_JSON      = BASE_DIR / "forecast_input_7days.json"


def build_forecast_input() -> dict:
    """
    Merge weather + calendar into a Prophet-ready feature DataFrame.
    Saves to forecast_input_7days.json and returns a summary dict.
    """
    # ── 1. Load weather ──────────────────────────────────────
    with open(WEATHER_JSON, "r", encoding="utf-8") as f:
        weather_records = json.load(f)

    df_weather = pd.DataFrame(weather_records)
    df_weather["ds"] = pd.to_datetime(df_weather["Date"] + " " + df_weather["Time"])

    # ── 2. Load calendar ─────────────────────────────────────
    with open(CALENDAR_JSON, "r", encoding="utf-8") as f:
        calendar_records = json.load(f)

    df_cal = pd.DataFrame(calendar_records)
    df_cal["date"] = pd.to_datetime(df_cal["date"]).dt.date

    # ── 3. Build calendar lookup dict {date → row} ───────────
    cal_lookup = {
        row["date"]: row
        for row in df_cal.to_dict(orient="records")
    }

    # ── 4. Merge on date ─────────────────────────────────────
    df_weather["_date"] = df_weather["ds"].dt.date
    df_weather["on_holiday"] = df_weather["_date"].map(
        lambda d: bool(cal_lookup.get(d, {}).get("on_holiday", False))
    )
    df_weather["on_weekend"] = df_weather["_date"].map(
        lambda d: bool(cal_lookup.get(d, {}).get("on_weekend", False))
    )
    df_weather["on_weekday"] = df_weather["_date"].map(
        lambda d: bool(cal_lookup.get(d, {}).get("on_weekday", True))
    )

    # ── 5. Derived columns matching Prophet training schema ──
    df_weather["is_public_holiday"] = df_weather["on_holiday"].astype(int)
    df_weather["is_weekend"]        = df_weather["on_weekend"].astype(int)
    df_weather["hour"]              = df_weather["ds"].dt.hour
    df_weather["day"]               = df_weather["ds"].dt.dayofweek   # 0=Mon … 6=Sun

    # Hour dummy columns: hour_0 … hour_23
    for h in range(24):
        df_weather[f"hour_{h}"] = (df_weather["hour"] == h).astype(int)

    # ── 6. Build final Prophet-ready DataFrame ───────────────
    prophet_df = df_weather[[
        "ds",
        "Temperature (°C)",
        "is_weekend",
        "is_public_holiday",
        "hour",
        "day",
        "on_holiday",
        "on_weekend",
        "on_weekday",
    ]].copy()

    # Rename temperature to match training column name
    prophet_df.rename(columns={"Temperature (°C)": "Temperature"}, inplace=True)

    # ds → ISO string for JSON serialisation
    prophet_df["ds"] = prophet_df["ds"].dt.strftime("%Y-%m-%d %H:%M:%S")

    # ── 7. Save ──────────────────────────────────────────────
    records = prophet_df.to_dict(orient="records")
    with open(OUTPUT_JSON, "w", encoding="utf-8") as f:
        json.dump(records, f, indent=2, ensure_ascii=False)

    summary = {
        "status":     "success",
        "built_at":   datetime.now().isoformat(),
        "records":    len(records),
        "output":     str(OUTPUT_JSON),
        "date_from":  records[0]["ds"][:10]  if records else None,
        "date_to":    records[-1]["ds"][:10] if records else None,
    }
    print(f"[forecast_builder] ✅ {len(records)} rows → {OUTPUT_JSON}")
    return summary


# ── Standalone run ────────────────────────────────────────────
if __name__ == "__main__":
    result = build_forecast_input()
    print("Done.", result)
