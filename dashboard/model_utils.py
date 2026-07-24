"""
model_utils.py
──────────────
Prophet model loading, scenario construction, and prediction helpers
for the Nugegoda Electricity Demand Forecasting Dashboard.
"""

import os
import json
import pandas as pd
import numpy as np
from prophet.serialize import model_from_json

# ──────────────────────────────────────────────
# Average temperature by hour (°C) — Nugegoda
# Tropical climate profile derived from training data
# ──────────────────────────────────────────────

AVG_TEMP_BY_HOUR = {
    0: 25.2, 1: 25, 2: 24.4, 3: 24.1, 4: 23.9, 5: 24,
    6: 22.6, 7: 25.0, 8: 26.4, 9: 28.5, 10: 30.4, 11: 31.8,
    12: 32.8, 13: 32.4, 14: 31.5, 15: 30.2, 16: 29.0, 17: 28.2,
    18: 26.4, 19: 26.1, 20: 26, 21: 25.4, 22: 25.1, 23: 25.5 
}

def add_day_type(df: pd.DataFrame) -> pd.DataFrame:
    """
    Add a 'day_type' column based on is_weekend and is_public_holiday flags.
    Values: 'holiday', 'weekend', or 'weekday'.
    """
    conditions = [
        df['is_public_holiday'] == 1,
        df['is_weekend'] == 1,
    ]
    choices = ['holiday', 'weekend']
    df['day_type'] = np.select(conditions, choices, default='weekday')
    return df


def load_model():
    """Load the serialized Prophet model from the JSON file."""
    model_path = os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        '..', 'electricityDemandModel.json'
    )
    with open(model_path, 'r') as f:
        model_json = f.read()
    model = model_from_json(model_json)
    return model


def make_scenario(model, scenario_name: str, is_weekend_val: int,
                  is_holiday_val: int, start_datetime: str = None,
                  periods: int = 24, freq: str = 'h') -> dict:
    """
    Build a future dataframe for a scenario and return predictions.

    Parameters
    ----------
    model : Prophet
        Fitted Prophet model.
    scenario_name : str
        Label for this scenario (e.g. 'Weekday', 'Weekend').
    is_weekend_val : int
        0 or 1.
    is_holiday_val : int
        0 or 1.
    start_datetime : str or None
        Start datetime string, e.g. '2026-06-05 00:00:00'.
        If None, uses next timestamp after training data.
    periods : int
        Number of hours to forecast.
    freq : str
        Frequency string (default 'H' for hourly).

    Returns
    -------
    dict with keys: dates, yhat, yhat_lower, yhat_upper, scenario,
                    peak_demand, peak_time, min_demand, min_time, avg_demand
    """
    if start_datetime is None:
        dummy_future = model.make_future_dataframe(
            periods=1, freq=freq, include_history=False
        )
        start_datetime = dummy_future['ds'].iloc[0]

    future = pd.DataFrame({
        'ds': pd.date_range(start=start_datetime, periods=periods, freq=freq)
    })

    future['hour'] = future['ds'].dt.hour
    future['day'] = future['ds'].dt.dayofweek
    future['Temperature'] = future['hour'].map(AVG_TEMP_BY_HOUR)
    future['is_weekend'] = is_weekend_val
    future['is_public_holiday'] = is_holiday_val
    future = add_day_type(future)
    future['on_weekday'] = (future['day_type'] == 'weekday').astype(int)
    future['on_weekend'] = (future['day_type'] == 'weekend').astype(int)
    future['on_holiday'] = (future['day_type'] == 'holiday').astype(int)
    future['off_holiday'] = 1 - future['on_holiday']

    # Add hour dummy regressors (1–23 to match model)
    for h in range(1, 24):
        future[f'hour_{h}'] = (future['hour'] == h).astype(int)

    forecast = model.predict(future)
    result = forecast[['ds', 'yhat', 'yhat_lower', 'yhat_upper']].copy()

    # Compute summary stats
    peak_idx = result['yhat'].idxmax()
    min_idx = result['yhat'].idxmin()

    return {
        'dates': result['ds'].dt.strftime('%Y-%m-%d %H:%M').tolist(),
        'yhat': result['yhat'].round(2).tolist(),
        'yhat_lower': result['yhat_lower'].round(2).tolist(),
        'yhat_upper': result['yhat_upper'].round(2).tolist(),
        'scenario': scenario_name,
        'peak_demand': round(result['yhat'].max(), 2),
        'peak_time': result.loc[peak_idx, 'ds'].strftime('%H:%M'),
        'min_demand': round(result['yhat'].min(), 2),
        'min_time': result.loc[min_idx, 'ds'].strftime('%H:%M'),
        'avg_demand': round(result['yhat'].mean(), 2),
    }


def make_7day_forecast(model, start_datetime: str = None) -> dict:
    """
    Generate a 7-day (168-hour) rolling forecast with automatic
    weekend detection per day.

    Parameters
    ----------
    model : Prophet
        Fitted Prophet model.
    start_datetime : str or None
        Start datetime. If None, uses next after training data.

    Returns
    -------
    dict with keys: dates, yhat, yhat_lower, yhat_upper,
                    peak_demand, peak_time, min_demand, min_time, avg_demand,
                    daily_peaks (list of per-day peak info)
    """
    periods = 168  # 7 days × 24 hours

    if start_datetime is None:
        dummy_future = model.make_future_dataframe(
            periods=1, freq='h', include_history=False
        )
        start_datetime = dummy_future['ds'].iloc[0]

    future = pd.DataFrame({
        'ds': pd.date_range(start=start_datetime, periods=periods, freq='h')
    })

    future['hour'] = future['ds'].dt.hour
    future['day'] = future['ds'].dt.dayofweek
    future['is_weekend'] = future['day'].apply(lambda x: 1 if x >= 5 else 0)
    future['is_public_holiday'] = 0
    future = add_day_type(future)
    future['on_weekday'] = (future['day_type'] == 'weekday').astype(int)
    future['on_weekend'] = (future['day_type'] == 'weekend').astype(int)
    future['on_holiday'] = (future['day_type'] == 'holiday').astype(int)
    future['off_holiday'] = 1 - future['on_holiday']

    for h in range(1, 24):
        future[f'hour_{h}'] = (future['hour'] == h).astype(int)

    forecast = model.predict(future)
    result = forecast[['ds', 'yhat', 'yhat_lower', 'yhat_upper']].tail(periods)

    # Per-day peak info
    result_copy = result.copy()
    result_copy['date'] = result_copy['ds'].dt.date
    daily_peaks = []
    for date, group in result_copy.groupby('date'):
        peak_idx = group['yhat'].idxmax()
        daily_peaks.append({
            'date': str(date),
            'day_name': group['ds'].iloc[0].strftime('%A'),
            'peak_demand': round(group['yhat'].max(), 2),
            'peak_time': group.loc[peak_idx, 'ds'].strftime('%H:%M'),
            'avg_demand': round(group['yhat'].mean(), 2),
            'is_weekend': 1 if group['ds'].iloc[0].dayofweek >= 5 else 0,
        })

    peak_idx = result['yhat'].idxmax()
    min_idx = result['yhat'].idxmin()

    return {
        'dates': result['ds'].dt.strftime('%Y-%m-%d %H:%M').tolist(),
        'yhat': result['yhat'].round(2).tolist(),
        'yhat_lower': result['yhat_lower'].round(2).tolist(),
        'yhat_upper': result['yhat_upper'].round(2).tolist(),
        'peak_demand': round(result['yhat'].max(), 2),
        'peak_time': result.loc[peak_idx, 'ds'].strftime('%Y-%m-%d %H:%M'),
        'min_demand': round(result['yhat'].min(), 2),
        'min_time': result.loc[min_idx, 'ds'].strftime('%Y-%m-%d %H:%M'),
        'avg_demand': round(result['yhat'].mean(), 2),
        'daily_peaks': daily_peaks,
    }
