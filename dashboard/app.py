"""
app.py
──────
Flask application for the Nugegoda Electricity Demand Forecasting Dashboard.
Serves the UI and provides REST API endpoints for Prophet model predictions.

Nightly Schedulers
──────────────────
Job 1 — 01:00 Asia/Colombo  : weather_sync.fetch_weather()
         Downloads 7-day hourly weather from Open-Meteo → weather_next_7_days.json

Job 2 — 01:02 Asia/Colombo  : forecast_builder.build_forecast_input()
         Merges weather JSON + calendar_2026.json into Prophet-ready feature
         matrix → forecast_input_7days.json

Both jobs also run once at startup so fresh data is always available.
"""

import os
os.environ['CUDA_VISIBLE_DEVICES'] = '-1'
os.environ['TF_CPP_MIN_LOG_LEVEL'] = '3'
from flask import Flask, render_template, request, jsonify
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
import pytz

from model_utils import load_model, make_scenario, make_7day_forecast
from weather_sync import fetch_weather
from forecast_builder import build_forecast_input
from solar_utils import get_solar_forecast, get_past_solar_data
import microzone_utils
app = Flask(__name__)

# ── Load Prophet model once at startup ──────────────────────
print("⚡ Loading Prophet model …")
model = load_model()
print("✅ Model loaded successfully!")


# ── Nightly Weather Sync — Job 1 (01:00 Asia/Colombo) ───────
def _safe_weather_sync():
    """Wrapper so a failed weather sync never crashes the scheduler."""
    try:
        result = fetch_weather()
        print(f"[scheduler] ✅ Weather sync OK — {result['records']} records @ {result['synced_at']}")
    except Exception as exc:
        print(f"[scheduler] ⚠️  Weather sync FAILED: {exc}")


# ── Forecast Input Builder — Job 2 (01:02 Asia/Colombo) ──────
def _safe_forecast_build():
    """Wrapper so a failed build never crashes the scheduler."""
    try:
        result = build_forecast_input()
        print(f"[scheduler] ✅ Forecast input built — {result['records']} rows @ {result['built_at']}")
    except Exception as exc:
        print(f"[scheduler] ⚠️  Forecast input build FAILED: {exc}")


# Only start the scheduler once (avoids double-run in Flask debug reloader)
if not app.debug or os.environ.get("WERKZEUG_RUN_MAIN") == "true":
    _tz = pytz.timezone("Asia/Colombo")
    scheduler = BackgroundScheduler(timezone=_tz)

    # Job 1 — Weather sync at 01:00 every night
    scheduler.add_job(
        _safe_weather_sync,
        trigger=CronTrigger(hour=10, minute=32, timezone=_tz),
        id="nightly_weather_sync",
        name="Nightly Weather Sync 01:00",
        replace_existing=True,
    )

    # Job 2 — Forecast input builder at 01:02 every night
    #          (2 min after weather sync so fresh data is ready)
    scheduler.add_job(
        _safe_forecast_build,
        trigger=CronTrigger(hour=10, minute=35, timezone=_tz),
        id="nightly_forecast_build",
        name="Nightly Forecast Input Build 01:10",
        replace_existing=True,
    )

    scheduler.start()
    print("🕐 Scheduler started:")
    print("   • Job 1 — Weather sync      → 01:00 Asia/Colombo")
    print("   • Job 2 — Forecast builder  → 01:10 Asia/Colombo")

    # Run both immediately on startup so data is always fresh
    print("🌤️  Running initial weather sync …")
    _safe_weather_sync()

    print("🔗 Running initial forecast input build …")
    _safe_forecast_build()


# ── Page routes ──────────────────────────────────────────────
@app.route('/')
def index():
    """Serve the main dashboard page."""
    return render_template('index.html')


# ── API: Weather data ────────────────────────────────────────
@app.route('/api/weather', methods=['GET'])
def api_weather():
    """
    Return the latest cached 7-day weather JSON.
    Triggers a fresh sync if the file is missing.
    """
    import json
    from pathlib import Path

    weather_file = Path(__file__).parent / "weather_next_7_days.json"
    if not weather_file.exists():
        _safe_weather_sync()

    with open(weather_file, "r", encoding="utf-8") as f:
        data = json.load(f)
    return jsonify(data)


@app.route('/api/weather/sync', methods=['POST'])
def api_weather_sync():
    """
    Manually trigger a weather data refresh (useful for testing).
    POST /api/weather/sync
    """
    try:
        result = fetch_weather()
        return jsonify({"status": "success", **result})
    except Exception as exc:
        return jsonify({"status": "error", "message": str(exc)}), 500


# ── API: Forecast Input (combined weather + calendar) ────────
@app.route('/api/forecast-input', methods=['GET'])
def api_forecast_input():
    """
    Return the latest Prophet-ready combined feature matrix JSON.
    Triggers a fresh build if the file is missing.
    GET /api/forecast-input
    """
    import json
    from pathlib import Path

    input_file = Path(__file__).parent / "forecast_input_7days.json"
    if not input_file.exists():
        _safe_forecast_build()

    with open(input_file, "r", encoding="utf-8") as f:
        data = json.load(f)
    return jsonify(data)


@app.route('/api/forecast-input/build', methods=['POST'])
def api_forecast_input_build():
    """
    Manually trigger a forecast input rebuild.
    POST /api/forecast-input/build
    """
    try:
        result = build_forecast_input()
        return jsonify({"status": "success", **result})
    except Exception as exc:
        return jsonify({"status": "error", "message": str(exc)}), 500

"""
Drop-in replacement for the /api/forecast/single route.
Paste this over the old route in your app.py (keep your existing
`model = Prophet(...)` load and `_safe_forecast_build()` helper above it).
"""

import json
import pytz
from pathlib import Path
from datetime import date as _date, datetime as _datetime
import pandas as pd

COLOMBO_TZ = pytz.timezone("Asia/Colombo")

REQUIRED_REGRESSORS = (
    ["Temperature", "is_weekend", "is_public_holiday", "day", "hour"]
    + [f"hour_{h}" for h in range(1, 24)]
)


def _load_forecast_rows():
    input_file = Path(__file__).parent / "forecast_input_7days.json"
    if not input_file.exists():
        _safe_forecast_build()
    with open(input_file, "r", encoding="utf-8") as f:
        return json.load(f)


def _temp_lookup(all_rows):
    """Build a (day_of_week, hour) -> Temperature lookup straight from
    forecast_input_7days.json, which already carries 'day' and 'hour' for
    every row. This gives an exact match keyed on the same weekday +
    time-of-day instead of a flat all-days average, since weekday and
    weekend temperature curves differ. Also returns a plain hour-only
    average as a last-resort fallback for a (day, hour) combo the file
    doesn't happen to contain."""
    df = pd.DataFrame(all_rows)
    df["hour"] = pd.to_datetime(df["ds"]).dt.hour
    df["day"]  = pd.to_datetime(df["ds"]).dt.dayofweek
    by_day_hour = df.groupby(["day", "hour"])["Temperature"].mean().to_dict()
    by_hour     = df.groupby("hour")["Temperature"].mean().to_dict()
    return by_day_hour, by_hour


def _build_synthetic_day(date_str, all_rows, holiday_dates=None):
    """Build a 24-row feature template for a date that isn't in
    forecast_input_7days.json, pulling temperature from the file's own
    (day-of-week, hour) records rather than a flat average."""
    holiday_dates = holiday_dates or set()
    by_day_hour, by_hour = _temp_lookup(all_rows)

    start = f"{date_str} 00:00:00"
    rows = []
    for ts in pd.date_range(start=start, periods=24, freq="h"):
        hour, dow = ts.hour, ts.dayofweek
        temp = by_day_hour.get((dow, hour), by_hour.get(hour, 27.0))
        rows.append({
            "ds": ts.strftime("%Y-%m-%d %H:%M:%S"),
            "Temperature": round(float(temp), 2),
            "is_weekend": 1 if dow >= 5 else 0,
            "is_public_holiday": 1 if date_str in holiday_dates else 0,
            "hour": hour,
            "day": dow,
        })
    return rows


@app.route('/api/forecast/single', methods=['POST'])
def api_single_forecast():
    """
    24-hour electricity demand forecast using real weather + calendar data.

    Request JSON (all fields optional):
        { "date": "2026-07-23" }
            omit "date" to default to TODAY in Asia/Colombo time

        { "rows": [ { "ds": "...", "Temperature": 28.1,
                      "is_weekend": 0, "is_public_holiday": 0 }, ... 24 items ] }
            supply your own 24-hour feature set directly (bypasses the
            forecast_input_7days.json file entirely) — equivalent to the
            future_24h_template you built in Colab.

        { "holidays": ["2026-07-25"] }
            optional list of dates to mark as public holidays when a date
            falls outside forecast_input_7days.json and has to be
            synthesized.

    Priority: custom "rows" > date found in forecast_input_7days.json >
    synthesized template using the file's own (day-of-week, hour)
    temperature records, not a flat all-days average.
    """
    req      = request.get_json() or {}
    date_str = (req.get('date') or '').strip()
    custom_rows   = req.get('rows')
    holiday_dates = set(req.get('holidays') or [])

    if not date_str:
        date_str = _datetime.now(COLOMBO_TZ).date().isoformat()

    all_rows = _load_forecast_rows()
    source = None

    # ── 1. Resolve the 24 rows to forecast ─────────────────────
    if custom_rows:
        if len(custom_rows) != 24:
            return jsonify({
                "error": f"'rows' must contain exactly 24 hourly entries, got {len(custom_rows)}."
            }), 400
        day_rows = custom_rows
        source = "custom_input"
        # infer date_str from the supplied rows for the response payload
        date_str = str(day_rows[0]["ds"])[:10]
    else:
        day_rows = [r for r in all_rows if r["ds"].startswith(date_str)]
        if day_rows:
            source = "forecast_input_7days.json"
        else:
            try:
                _date.fromisoformat(date_str)
            except ValueError:
                return jsonify({"error": f"'{date_str}' is not a valid YYYY-MM-DD date."}), 400
            day_rows = _build_synthetic_day(date_str, all_rows, holiday_dates)
            source = "extrapolated_day_hour_matched"

    # ── 2. Build the Prophet future DataFrame ──────────────────
    future = pd.DataFrame(day_rows)
    future["ds"] = pd.to_datetime(future["ds"])
    future = future.sort_values("ds").reset_index(drop=True)

    # 2a. Time features always re-derived from ds — never trusted from input
    future["hour"] = future["ds"].dt.hour
    future["day"]  = future["ds"].dt.dayofweek

    # 2b. Calendar flags → strict 0 / 1
    for col in ("is_weekend", "is_public_holiday"):
        if col not in future.columns:
            future[col] = 0
        future[col] = pd.to_numeric(future[col], errors="coerce").fillna(0).astype(int)

    # 2c. Conditional-seasonality columns, always derived fresh from the
    #     calendar flags so they can never disagree with is_weekend/is_public_holiday
    future["on_holiday"] = future["is_public_holiday"].astype(bool)
    future["on_weekend"] = future["is_weekend"].astype(bool) & ~future["on_holiday"]
    future["on_weekday"] = ~future["on_weekend"] & ~future["on_holiday"]

    # 2d. Temperature — fill any NaN with tropical fallback
    future["Temperature"] = pd.to_numeric(future["Temperature"], errors="coerce").fillna(27.0)

    # 2e. Hour dummies: ALWAYS rebuilt from the true hour (fixes stale/mismatched
    #     hour_N values that could previously slip through unchanged)
    for h in range(24):
        future[f"hour_{h}"] = (future["hour"] == h).astype(int)

    # 2f. Sanity check before predicting — fail loudly instead of Prophet
    #     silently producing a degraded forecast from a missing regressor
    missing = [c for c in REQUIRED_REGRESSORS if c not in future.columns]
    if missing:
        return jsonify({"error": f"Missing required regressor columns: {missing}"}), 500
    if len(future) != 24:
        return jsonify({"error": f"Expected 24 hourly rows, got {len(future)}."}), 400

    # ── 3. Build model_inputs — matches exact training CSV format ──────
    TRAINING_COLS = (
        ["ds", "day", "hour", "is_weekend", "is_public_holiday", "Temperature"]
        + [f"hour_{h}" for h in range(24)]
    )
    CONDITION_COLS = ["on_weekday", "on_weekend", "on_holiday"]
    ALL_INPUT_COLS = TRAINING_COLS + CONDITION_COLS

    model_inputs = future[ALL_INPUT_COLS].copy()
    model_inputs["ds"] = model_inputs["ds"].dt.strftime("%Y-%m-%d %H:%M:%S")
    model_inputs.insert(0, "index", range(len(model_inputs)))
    model_inputs_list = model_inputs.to_dict(orient="records")

    # ── 4. Run Prophet prediction ───────────────────────────────
    forecast = model.predict(future)
    result   = forecast[["ds", "yhat", "yhat_lower", "yhat_upper"]].copy()
    result["yhat"] = result["yhat"].clip(lower=0)          # demand can't be negative
    result["yhat_lower"] = result["yhat_lower"].clip(lower=0)

    # ── 5. Day-type label ────────────────────────────────────────
    if future.loc[0, "on_holiday"]:
        scenario = "Public Holiday"
    elif future.loc[0, "on_weekend"]:
        scenario = "Weekend"
    else:
        scenario = "Weekday"

    # ── 6. Summary stats ─────────────────────────────────────────
    peak_idx = result["yhat"].idxmax()
    min_idx  = result["yhat"].idxmin()

    return jsonify({
        "dates":       result["ds"].dt.strftime("%Y-%m-%d %H:%M").tolist(),
        "yhat":        result["yhat"].round(2).tolist(),
        "yhat_lower":  result["yhat_lower"].round(2).tolist(),
        "yhat_upper":  result["yhat_upper"].round(2).tolist(),

        "scenario":    scenario,
        "date":        date_str,
        "data_source": source,   # "forecast_input_7days.json" | "custom_input" | "extrapolated_day_hour_matched"

        "peak_demand": round(result["yhat"].max(), 2),
        "peak_time":   result.loc[peak_idx, "ds"].strftime("%H:%M"),
        "min_demand":  round(result["yhat"].min(), 2),
        "min_time":    result.loc[min_idx,  "ds"].strftime("%H:%M"),
        "avg_demand":  round(result["yhat"].mean(), 2),

        "model_inputs": model_inputs_list,
    })


# ── API: Scenario Comparison (Compare Weekday, Weekend, Holiday) ──
@app.route('/api/forecast/compare', methods=['POST'])
def api_compare_scenarios():
    """
    Compare all 3 scenarios (weekday, weekend, holiday) for the same date.

    Request JSON:
        { "date": "2026-07-24" }

    Returns JSON with an array of 3 scenario results.
    """
    data = request.get_json() or {}
    date_str = data.get('date', '').strip()
    if not date_str:
        date_str = _datetime.now(COLOMBO_TZ).date().isoformat()

    start_dt = f"{date_str} 00:00:00"

    scenarios = [
        ('Weekday',        0, 0),
        ('Weekend',        1, 0),
        ('Public Holiday', 0, 1),
    ]

    results = []
    for name, is_wknd, is_hol in scenarios:
        result = make_scenario(
            model, name,
            is_weekend_val=is_wknd,
            is_holiday_val=is_hol,
            start_datetime=start_dt,
            periods=24
        )
        results.append(result)

    return jsonify({'date': date_str, 'scenarios': results})


# ── API: 7-Day Demand Forecast ──────────────────────────────
@app.route('/api/forecast/7day', methods=['GET', 'POST'])
def api_7day_forecast():
    """
    7-Day electricity demand forecast using weather + calendar data from forecast_input_7days.json.
    Computes daily totals, peaks, averages, and returns 7-day hourly & daily summary data.
    """
    import json
    from pathlib import Path
    import pandas as pd

    input_file = Path(__file__).parent / "forecast_input_7days.json"
    if not input_file.exists():
        _safe_forecast_build()

    with open(input_file, "r", encoding="utf-8") as f:
        all_rows = json.load(f)

    future = pd.DataFrame(all_rows)
    future["ds"] = pd.to_datetime(future["ds"])
    future = future.sort_values("ds").reset_index(drop=True)

    future["hour"] = future["ds"].dt.hour
    future["day"]  = future["ds"].dt.dayofweek

    for col in ("is_weekend", "is_public_holiday"):
        if col not in future.columns:
            future[col] = 0
        future[col] = pd.to_numeric(future[col], errors="coerce").fillna(0).astype(int)

    future["on_holiday"] = future["is_public_holiday"].astype(bool)
    future["on_weekend"] = future["is_weekend"].astype(bool) & ~future["on_holiday"]
    future["on_weekday"] = ~future["on_weekend"] & ~future["on_holiday"]
    future["Temperature"] = pd.to_numeric(future["Temperature"], errors="coerce").fillna(27.0)

    for h in range(24):
        future[f"hour_{h}"] = (future["hour"] == h).astype(int)

    forecast = model.predict(future)
    future["yhat"] = forecast["yhat"].clip(lower=0).round(2)
    future["yhat_lower"] = forecast["yhat_lower"].clip(lower=0).round(2)
    future["yhat_upper"] = forecast["yhat_upper"].clip(lower=0).round(2)
    future["date"] = future["ds"].dt.strftime("%Y-%m-%d")

    # Aggregate by date for 7 daily totals
    daily_summaries = []
    for date_str, group in future.groupby("date", sort=False):
        total_demand = round(float(group["yhat"].sum()), 2)
        peak_idx = group["yhat"].idxmax()
        min_idx = group["yhat"].idxmin()
        day_name = group["ds"].iloc[0].strftime("%A")

        if group["on_holiday"].any():
            day_type = "Public Holiday"
        elif group["on_weekend"].any():
            day_type = "Weekend"
        else:
            day_type = "Weekday"

        daily_summaries.append({
            "date": date_str,
            "day_name": day_name,
            "day_type": day_type,
            "total_demand_kwh": total_demand,
            "peak_demand_kw": round(float(group["yhat"].max()), 2),
            "peak_time": group.loc[peak_idx, "ds"].strftime("%H:%M"),
            "min_demand_kw": round(float(group["yhat"].min()), 2),
            "min_time": group.loc[min_idx, "ds"].strftime("%H:%M"),
            "avg_demand_kw": round(float(group["yhat"].mean()), 2),
            "avg_temp": round(float(group["Temperature"].mean()), 1),
            "hourly": [
                {
                    "time": row["ds"].strftime("%H:%M"),
                    "yhat": row["yhat"],
                    "temp": row["Temperature"]
                }
                for _, row in group.iterrows()
            ]
        })

    total_7day_demand = round(float(future["yhat"].sum()), 2)

    return jsonify({
        "status": "success",
        "total_7day_demand_kwh": total_7day_demand,
        "daily": daily_summaries,
        "hourly_all": [
            {
                "ds": row["ds"].strftime("%Y-%m-%d %H:%M"),
                "yhat": row["yhat"],
                "temp": row["Temperature"]
            }
            for _, row in future.iterrows()
        ]
    })


# ── API: 7-Day Solar Export Generation Forecast ──────────────
@app.route('/api/solar/forecast', methods=['GET'])
def api_solar_forecast():
    """
    7-day (168-hour) solar export forecast calculated using the trained
    dual-input Keras model, past 7 days export/weather, and future weather forecast.
    """
    try:
        result = get_solar_forecast()
        return jsonify(result)
    except Exception as exc:
        import traceback
        traceback.print_exc()
        return jsonify({"status": "error", "message": str(exc)}), 500


@app.route('/api/solar/past', methods=['GET'])
def api_solar_past():
    """
    Return historical past 7 days (168 hours) solar export data.
    """
    try:
        result = get_past_solar_data()
        return jsonify(result)
    except Exception as exc:
        import traceback
        traceback.print_exc()
        return jsonify({"status": "error", "message": str(exc)}), 500


# ── API: Micro-Zone Analysis (Module C) ──────────────────────
# Read-only: serves the precomputed pipeline output from module_c/artifacts/.
# The next-day forecast in this payload is frozen at the training dataset's last
# date (Module C has no live smart-meter feed to re-forecast against) — see
# microzone_utils.py for why, and POST /api/microzone/rebuild to refresh it.

@app.route('/api/microzone/zones', methods=['GET'])
def api_microzone_zones():
    """Zone metadata, centroids and polygons for the map."""
    try:
        return jsonify(microzone_utils.get_zones())
    except microzone_utils.MicrozoneArtifactsMissing as exc:
        return jsonify({"error": str(exc)}), 404


@app.route('/api/microzone/overview', methods=['GET'])
def api_microzone_overview():
    """Full per-zone analysis: forecast, confidence, risk, warning, trend, priority list."""
    try:
        return jsonify(microzone_utils.get_overview())
    except microzone_utils.MicrozoneArtifactsMissing as exc:
        return jsonify({"error": str(exc)}), 404


@app.route('/api/microzone/zone/<int:zone_id>', methods=['GET'])
def api_microzone_zone_detail(zone_id):
    """Merged metadata + analysis for a single zone."""
    try:
        detail = microzone_utils.get_zone_detail(zone_id)
    except microzone_utils.MicrozoneArtifactsMissing as exc:
        return jsonify({"error": str(exc)}), 404
    if detail is None:
        return jsonify({"error": f"Zone {zone_id} not found"}), 404
    return jsonify(detail)


@app.route('/api/microzone/validation', methods=['GET'])
def api_microzone_validation():
    """Stage 8.2/8.3 — worst-day replay + historical validation ('does the
    detector actually work?'), precomputed offline."""
    try:
        return jsonify(microzone_utils.get_validation())
    except microzone_utils.MicrozoneArtifactsMissing as exc:
        return jsonify({"error": str(exc)}), 404


@app.route('/api/microzone/scenario', methods=['POST'])
def api_microzone_scenario():
    """
    Stage 8.1 — scenario-injection what-if. Request JSON: { "zone": 0, "surge_pct": 35 }.
    Pure arithmetic on precomputed numbers (see microzone_utils.run_scenario) — no
    model inference, so this works without xgboost/sklearn installed.
    """
    req = request.get_json() or {}
    try:
        zone_id = int(req.get('zone'))
        surge_pct = float(req.get('surge_pct'))
    except (TypeError, ValueError):
        return jsonify({"error": "'zone' (int) and 'surge_pct' (number) are required"}), 400

    try:
        result = microzone_utils.run_scenario(zone_id, surge_pct)
    except microzone_utils.MicrozoneArtifactsMissing as exc:
        return jsonify({"error": str(exc)}), 404

    if result is None:
        return jsonify({"error": f"Zone {zone_id} not found"}), 404
    return jsonify(result)


@app.route('/api/microzone/scenario/multi', methods=['POST'])
def api_microzone_scenario_multi():
    """
    Multi-zone scenario injection. Request JSON: { "surges": { "0": 20, "1": 50, "2": 0 } }
    — applies a (possibly different) surge % to every zone at once and returns the
    re-ranked priority list under that combined scenario. Pure arithmetic on
    precomputed numbers (see microzone_utils.run_scenario_multi) — no model
    inference, so this works without xgboost/sklearn installed.
    """
    req = request.get_json() or {}
    surges = req.get('surges') or {}
    try:
        surges = {int(k): float(v) for k, v in surges.items()}
    except (TypeError, ValueError):
        return jsonify({"error": "'surges' must be a { zone_id: surge_pct } object"}), 400

    try:
        result = microzone_utils.run_scenario_multi(surges)
    except microzone_utils.MicrozoneArtifactsMissing as exc:
        return jsonify({"error": str(exc)}), 404
    return jsonify(result)


@app.route('/api/microzone/rebuild', methods=['POST'])
def api_microzone_rebuild():
    """
    Manually re-run the Module C pipeline (requires module_c/requirements-train.txt
    installed). Maintenance endpoint — not called by the dashboard UI automatically.
    """
    try:
        result = microzone_utils.rebuild_pipeline()
        return jsonify(result)
    except Exception as exc:
        return jsonify({"status": "error", "message": str(exc)}), 500


# @app.route('/api/forecast/single', methods=['POST'])
# def api_single_forecast():
#     """
#     Single-day 24-hour forecast for one scenario.

#     Request JSON:
#         { "date": "2026-06-05", "scenario": "weekday"|"weekend"|"holiday" }

#     Returns JSON with forecast data and summary stats.
#     """
#     data = request.get_json()
#     date_str = data.get('date', '2026-07-24')
#     scenario = data.get('scenario', 'weekday')

#     start_dt = f"{date_str} 00:00:00"

#     scenario_map = {
#         'weekday':  ('Weekday',        0, 0),
#         'weekend':  ('Weekend',        1, 0),
#         'holiday':  ('Public Holiday', 0, 1),
#     }

#     name, is_wknd, is_hol = scenario_map.get(scenario, ('Weekday', 0, 0))

#     result = make_scenario(
#         model, name,
#         is_weekend_val=is_wknd,
#         is_holiday_val=is_hol,
#         start_datetime=start_dt,
#         periods=24
#     )

#     return jsonify(result)

# @app.route('/api/forecast/single', methods=['POST'])
# def api_single_forecast():
#     """
#     Predict 24-hour electricity demand.

#     Request:
#     {
#         "date":"2026-04-06"
#     }

#     If date is omitted, defaults to 2026-04-06.
#     """

#     req = request.get_json(silent=True) or {}

#     prediction_date = req.get("date", "2026-06-06")

#     # -----------------------------
#     # Hardcoded 24-hour temperature
#     # -----------------------------
#     temperature_array = [
#         27.6,27.4,27.0,26.7,26.3,26.0,
#         26.0,27.2,28.5,29.5,29.9,30.3,
#         30.5,30.4,30.2,29.8,29.4,29.0,
#         28.3,28.2,27.9,27.8,27.5,27.2
#     ]

#     if len(temperature_array) != 24:
#         return jsonify({
#             "error":"Temperature array must contain exactly 24 values."
#         }),400

#     # -----------------------------
#     # Build future dataframe
#     # -----------------------------
#     future = pd.DataFrame({
#         "ds": pd.date_range(
#             start=f"{prediction_date} 00:00:00",
#             periods=24,
#             freq="H"
#         )
#     })

#     # -----------------------------
#     # Time Features
#     # -----------------------------
#     future["day"] = future["ds"].dt.dayofweek
#     future["hour"] = future["ds"].dt.hour

#     # -----------------------------
#     # Calendar
#     # -----------------------------
#     future["is_weekend"] = (
#         future["day"] >= 5
#     ).astype(int)

#     future["is_public_holiday"] = 0

#     # -----------------------------
#     # Conditional seasonalities
#     # -----------------------------
#     future["on_holiday"] = future["is_public_holiday"].astype(bool)

#     future["on_weekend"] = (
#         future["is_weekend"].astype(bool)
#         &
#         ~future["on_holiday"]
#     )

#     future["on_weekday"] = (
#         ~future["on_weekend"]
#         &
#         ~future["on_holiday"]
#     )

#     # -----------------------------
#     # Temperature
#     # -----------------------------
#     future["Temperature"] = temperature_array

#     # -----------------------------
#     # Hour Dummy Variables
#     # -----------------------------
#     for h in range(24):
#         future[f"hour_{h}"] = (
#             future["hour"] == h
#         ).astype(int)

#     # -----------------------------
#     # Verify regressors
#     # -----------------------------
#     missing = [
#         c for c in REQUIRED_REGRESSORS
#         if c not in future.columns
#     ]

#     if missing:
#         return jsonify({
#             "error": f"Missing regressors: {missing}"
#         }),500

#     # -----------------------------
#     # Prophet Prediction
#     # -----------------------------
#     forecast = model.predict(future)

#     result = forecast[
#         [
#             "ds",
#             "yhat",
#             "yhat_lower",
#             "yhat_upper"
#         ]
#     ].copy()

#     result["yhat"] = result["yhat"].clip(lower=0)
#     result["yhat_lower"] = result["yhat_lower"].clip(lower=0)

#     peak_idx = result["yhat"].idxmax()
#     min_idx = result["yhat"].idxmin()

#     scenario = (
#         "Weekend"
#         if future.loc[0, "is_weekend"] == 1
#         else "Weekday"
#     )

#     # -----------------------------
#     # Training-format inputs
#     # -----------------------------
#     TRAINING_COLS = (
#         [
#             "ds",
#             "day",
#             "hour",
#             "is_weekend",
#             "is_public_holiday",
#             "Temperature"
#         ]
#         +
#         [f"hour_{h}" for h in range(24)]
#     )

#     CONDITION_COLS = [
#         "on_weekday",
#         "on_weekend",
#         "on_holiday"
#     ]

#     model_inputs = future[
#         TRAINING_COLS + CONDITION_COLS
#     ].copy()

#     model_inputs["ds"] = model_inputs["ds"].dt.strftime(
#         "%Y-%m-%d %H:%M:%S"
#     )

#     model_inputs.insert(
#         0,
#         "index",
#         range(len(model_inputs))
#     )

#     return jsonify({

#         "date": prediction_date,

#         "scenario": scenario,

#         "dates":
#             result["ds"]
#             .dt.strftime("%Y-%m-%d %H:%M")
#             .tolist(),

#         "yhat":
#             result["yhat"]
#             .round(2)
#             .tolist(),

#         "yhat_lower":
#             result["yhat_lower"]
#             .round(2)
#             .tolist(),

#         "yhat_upper":
#             result["yhat_upper"]
#             .round(2)
#             .tolist(),

#         "peak_demand":
#             round(result["yhat"].max(),2),

#         "peak_time":
#             result.loc[
#                 peak_idx,
#                 "ds"
#             ].strftime("%H:%M"),

#         "min_demand":
#             round(result["yhat"].min(),2),

#         "min_time":
#             result.loc[
#                 min_idx,
#                 "ds"
#             ].strftime("%H:%M"),

#         "avg_demand":
#             round(result["yhat"].mean(),2),

#         "weather":[
#             {
#                 "time":
#                     future.loc[i,"ds"].strftime("%H:%M"),
#                 "temperature":
#                     float(future.loc[i,"Temperature"])
#             }
#             for i in range(24)
#         ],

#         "model_inputs":
#             model_inputs.to_dict(
#                 orient="records"
#             )
#     })

# ── Run ──────────────────────────────────────────────────────
if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5071)
