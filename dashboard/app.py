"""
app.py
──────
Flask application for the Nugegoda Electricity Demand Forecasting Dashboard.
Serves the UI and provides REST API endpoints for Prophet model predictions.
"""

from flask import Flask, render_template, request, jsonify
from model_utils import load_model, make_scenario, make_7day_forecast

app = Flask(__name__)

# ── Load model once at startup ──────────────────────────────
print("⚡ Loading Prophet model …")
model = load_model()
print("✅ Model loaded successfully!")


# ── Page routes ─────────────────────────────────────────────
@app.route('/')
def index():
    """Serve the main dashboard page."""
    return render_template('index.html')


# ── API routes ──────────────────────────────────────────────
@app.route('/api/forecast/single', methods=['POST'])
def api_single_forecast():
    """
    Single-day 24-hour forecast for one scenario.

    Request JSON:
        { "date": "2026-06-05", "scenario": "weekday"|"weekend"|"holiday" }

    Returns JSON with forecast data and summary stats.
    """
    data = request.get_json()
    date_str = data.get('date', '2026-06-05')
    scenario = data.get('scenario', 'weekday')

    start_dt = f"{date_str} 00:00:00"

    scenario_map = {
        'weekday':  ('Weekday',        0, 0),
        'weekend':  ('Weekend',        1, 0),
        'holiday':  ('Public Holiday', 0, 1),
    }

    name, is_wknd, is_hol = scenario_map.get(scenario, ('Weekday', 0, 0))

    result = make_scenario(
        model, name,
        is_weekend_val=is_wknd,
        is_holiday_val=is_hol,
        start_datetime=start_dt,
        periods=24
    )

    return jsonify(result)


@app.route('/api/forecast/compare', methods=['POST'])
def api_compare_scenarios():
    """
    Compare all 3 scenarios (weekday, weekend, holiday) for the same date.

    Request JSON:
        { "date": "2026-06-05" }

    Returns JSON with an array of 3 scenario results.
    """
    data = request.get_json()
    date_str = data.get('date', '2026-06-05')
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

    return jsonify({'scenarios': results})


@app.route('/api/forecast/weekly', methods=['POST'])
def api_weekly_forecast():
    """
    7-day (168-hour) rolling forecast.

    Request JSON:
        { "date": "2026-06-05" }

    Returns JSON with full 7-day forecast and daily peak summaries.
    """
    data = request.get_json()
    date_str = data.get('date', '2026-06-05')
    start_dt = f"{date_str} 00:00:00"

    result = make_7day_forecast(model, start_datetime=start_dt)

    return jsonify(result)


# ── Run ─────────────────────────────────────────────────────
if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5071)
