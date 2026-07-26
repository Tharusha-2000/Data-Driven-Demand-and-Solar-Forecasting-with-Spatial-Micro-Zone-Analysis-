"""
solar_utils.py
──────────────
Solar generation export forecasting helper module for Nugegoda Dashboard.
Uses a dual-input Keras model (solar_export_model.keras) trained on past
export/weather features and future weather forecasts to predict 7-day (168-hour)
solar export kW.
"""

import os
import json
import pickle
from pathlib import Path
import numpy as np
import pandas as pd

# Force CPU only to prevent CUDA driver initialization warnings/errors
os.environ['CUDA_VISIBLE_DEVICES'] = '-1'
os.environ['TF_CPP_MIN_LOG_LEVEL'] = '3'

BASE_DIR = Path(__file__).parent
SOLAR_MODELS_DIR = BASE_DIR / "solar_models"
PAST_JSON = BASE_DIR / "past_7_days.json"
FUTURE_JSON = BASE_DIR / "weather_next_7_days.json"

MODEL_PATH = SOLAR_MODELS_DIR / "solar_export_model.keras"
PAST_SCALER_PATH = SOLAR_MODELS_DIR / "past_scaler.pkl"
Y_SCALER_PATH = SOLAR_MODELS_DIR / "y_scaler.pkl"
FUTURE_SCALER_PATH = SOLAR_MODELS_DIR / "future_scaler.pkl"

# Global lazy-loaded objects
_model = None
_past_scaler = None
_future_scaler = None
_y_scaler = None


class ColumnSubsetScaler:
    """Wrapper scaler that transforms a subset of columns using a fitted full scaler."""
    def __init__(self, full_scaler, full_feature_names, target_feature_names):
        self.full_scaler = full_scaler
        self.full_feature_names = list(full_feature_names)
        self.target_feature_names = list(target_feature_names)

    def transform(self, df):
        full_df = pd.DataFrame(index=df.index)
        for col in self.full_feature_names:
            if col in df.columns:
                full_df[col] = df[col]
            else:
                full_df[col] = 0.0
        scaled = self.full_scaler.transform(full_df[self.full_feature_names])
        scaled_df = pd.DataFrame(scaled, index=df.index, columns=self.full_feature_names)
        return scaled_df[self.target_feature_names].values


def _load_scaler_file(file_path):
    """Load scaler file using joblib first (handles joblib dumps & STACK_GLOBAL pickle issues), falling back to standard pickle."""
    try:
        import joblib
        return joblib.load(str(file_path))
    except Exception:
        try:
            with open(file_path, "rb") as f:
                return pickle.load(f)
        except Exception:
            with open(file_path, "rb") as f:
                return pickle.load(f, encoding="latin1")


def load_solar_artifacts():
    """Load Keras model and scikit-learn scalers from disk."""
    global _model, _past_scaler, _future_scaler, _y_scaler

    if _model is not None:
        return _model, _past_scaler, _future_scaler, _y_scaler

    print("☀️ Loading Solar Export Keras model and scalers …")
    import tensorflow as tf

    if not MODEL_PATH.exists():
        raise FileNotFoundError(f"Solar model file missing at {MODEL_PATH}")

    _model = tf.keras.models.load_model(str(MODEL_PATH))

    _past_scaler = _load_scaler_file(PAST_SCALER_PATH)
    _y_scaler = _load_scaler_file(Y_SCALER_PATH)

    if FUTURE_SCALER_PATH.exists():
        _future_scaler = _load_scaler_file(FUTURE_SCALER_PATH)
    else:
        # If future_scaler.pkl doesn't exist separately, check if past_scaler is a dict
        if isinstance(_past_scaler, dict) and "future_scaler" in _past_scaler:
            _future_scaler = _past_scaler["future_scaler"]
            _past_scaler = _past_scaler["past_scaler"]
        else:
            # Construct ColumnSubsetScaler fallback if feature counts differ
            past_features = getattr(_past_scaler, "feature_names_in_", ["EXPORT_KW", "TEMP", "CLOUD", "GHI", "hour_sin", "hour_cos", "is_day"])
            future_features = ["TEMP", "CLOUD", "GHI", "hour_sin", "hour_cos", "is_day"]
            _future_scaler = ColumnSubsetScaler(_past_scaler, past_features, future_features)

    print("✅ Solar model and scalers loaded successfully!")
    return _model, _past_scaler, _future_scaler, _y_scaler


def predict_next_7_days(
    past_df,
    future_df,
    model,
    past_scaler,
    future_scaler,
    y_scaler,
    past_features,
    future_features
):
    """
    Predict next 7 days (168 hours) solar export kW given past 168h and future 168h features.
    """
    past_df = past_df.copy()
    future_df = future_df.copy()

    # Ensure timestamp order
    past_df = past_df.sort_index()
    future_df = future_df.sort_index()

    # Check row counts
    if len(past_df) != 168:
        raise ValueError(
            f"Expected 168 past rows, received {len(past_df)}."
        )

    if len(future_df) != 168:
        raise ValueError(
            f"Expected 168 future rows, received {len(future_df)}."
        )

    # Check required raw columns
    required_past = {
        "EXPORT_KW",
        "TEMP",
        "CLOUD",
        "GHI"
    }

    required_future = {
        "TEMP",
        "CLOUD",
        "GHI"
    }

    missing_past = required_past - set(past_df.columns)
    missing_future = required_future - set(future_df.columns)

    if missing_past:
        raise ValueError(
            f"Missing past columns: {missing_past}"
        )

    if missing_future:
        raise ValueError(
            f"Missing future columns: {missing_future}"
        )

    # Create time features
    for frame in [past_df, future_df]:
        frame["hour"] = frame.index.hour

        frame["hour_sin"] = np.sin(
            2 * np.pi * frame["hour"] / 24
        )

        frame["hour_cos"] = np.cos(
            2 * np.pi * frame["hour"] / 24
        )

        frame["is_day"] = (
            frame["GHI"] > 0
        ).astype(int)

    # Check missing values
    if past_df[past_features].isna().any().any():
        raise ValueError(
            "Past input contains missing values."
        )

    if future_df[future_features].isna().any().any():
        raise ValueError(
            "Future input contains missing values."
        )

    # Scale using training scalers
    X_past_scaled = past_scaler.transform(
        past_df[past_features]
    )

    X_future_scaled = future_scaler.transform(
        future_df[future_features]
    )

    # Add batch dimension
    X_past_inference = X_past_scaled.reshape(
        1,
        168,
        len(past_features)
    )

    X_future_inference = X_future_scaled.reshape(
        1,
        168,
        len(future_features)
    )

    # Predict scaled EXPORT_KW
    prediction_scaled = model.predict(
        [
            X_past_inference,
            X_future_inference
        ],
        verbose=0
    )

    # Convert back to kW
    prediction_kw = y_scaler.inverse_transform(
        prediction_scaled.reshape(-1, 1)
    ).reshape(-1)

    # Remove negative values
    prediction_kw = np.clip(
        prediction_kw,
        0,
        None
    )

    # Force nighttime output to zero
    night_mask = (
        future_df["is_day"].to_numpy() == 0
    )

    prediction_kw[night_mask] = 0

    # Return timestamped output
    result = pd.DataFrame(
        {
            "TIMESTAMP": future_df.index,
            "PREDICTED_EXPORT_KW": prediction_kw
        }
    )

    return result


def prepare_past_dataframe():
    """Load past_7_days.json into DataFrame with DatetimeIndex and standard column names."""
    if not PAST_JSON.exists():
        raise FileNotFoundError(f"Past dataset missing at {PAST_JSON}")

    with open(PAST_JSON, "r", encoding="utf-8") as f:
        records = json.load(f)

    df = pd.DataFrame(records)
    df["TIMESTAMP"] = pd.to_datetime(df["Date"] + " " + df["Time"])
    df.set_index("TIMESTAMP", inplace=True)
    df.sort_index(inplace=True)

    df.rename(columns={
        "Temperature (°C)": "TEMP",
        "Cloud Cover (%)": "CLOUD",
        "Solar Radiation (W/m²)": "GHI"
    }, inplace=True)

    return df


def prepare_future_dataframe():
    """Load weather_next_7_days.json into DataFrame with DatetimeIndex and standard column names."""
    if not FUTURE_JSON.exists():
        raise FileNotFoundError(f"Future weather dataset missing at {FUTURE_JSON}")

    with open(FUTURE_JSON, "r", encoding="utf-8") as f:
        records = json.load(f)

    df = pd.DataFrame(records)
    df["TIMESTAMP"] = pd.to_datetime(df["Date"] + " " + df["Time"])
    df.set_index("TIMESTAMP", inplace=True)
    df.sort_index(inplace=True)

    df.rename(columns={
        "Temperature (°C)": "TEMP",
        "Cloud Cover (%)": "CLOUD",
        "Solar Radiation (W/m²)": "GHI"
    }, inplace=True)

    return df


def _save_predictions_to_past_json(combined_df):
    """
    Save predicted solar export + weather data into past_7_days.json.
    This overwrites the file so the next forecast cycle uses these
    predictions as the historical 'past 7 days' input.
    Format matches the existing past_7_days.json schema:
    { Date, Time, EXPORT_KW, Temperature (°C), Cloud Cover (%), Solar Radiation (W/m²) }
    """
    records = []
    for _, row in combined_df.iterrows():
        records.append({
            "Date": row["TIMESTAMP"].strftime("%Y-%m-%d"),
            "Time": row["TIMESTAMP"].strftime("%H:%M"),
            "EXPORT_KW": round(float(row["PREDICTED_EXPORT_KW"]), 2),
            "Temperature (°C)": round(float(row["TEMP"]), 1),
            "Cloud Cover (%)": int(row["CLOUD"]),
            "Solar Radiation (W/m²)": round(float(row["GHI"]), 1)
        })

    with open(PAST_JSON, "w", encoding="utf-8") as f:
        json.dump(records, f, indent=2, ensure_ascii=False)

    print(f"💾 Saved {len(records)} predicted rows → {PAST_JSON}")


def get_solar_forecast():
    """
    Run full 7-day solar export prediction pipeline and return structured summary dict for API response.
    """
    model, past_scaler, future_scaler, y_scaler = load_solar_artifacts()

    past_df = prepare_past_dataframe()
    future_df = prepare_future_dataframe()

    # Determine feature names from scalers if available, or fall back to standard feature sets
    if hasattr(past_scaler, "feature_names_in_"):
        past_features = list(past_scaler.feature_names_in_)
    else:
        past_features = ["EXPORT_KW", "TEMP", "CLOUD", "GHI", "hour_sin", "hour_cos", "is_day"]

    if hasattr(future_scaler, "feature_names_in_"):
        future_features = list(future_scaler.feature_names_in_)
    else:
        future_features = ["TEMP", "CLOUD", "GHI", "hour_sin", "hour_cos", "is_day"]

    # Run inference
    forecast_df = predict_next_7_days(
        past_df=past_df,
        future_df=future_df,
        model=model,
        past_scaler=past_scaler,
        future_scaler=future_scaler,
        y_scaler=y_scaler,
        past_features=past_features,
        future_features=future_features
    )

    # Attach weather features to forecast dataframe for visualization & tabular display
    combined_df = forecast_df.copy()
    combined_df["TEMP"] = future_df["TEMP"].values
    combined_df["CLOUD"] = future_df["CLOUD"].values
    combined_df["GHI"] = future_df["GHI"].values
    combined_df["DATE"] = combined_df["TIMESTAMP"].dt.strftime("%Y-%m-%d")
    combined_df["TIME"] = combined_df["TIMESTAMP"].dt.strftime("%H:%M")

    # Summary Statistics
    total_7day_export_kwh = round(float(combined_df["PREDICTED_EXPORT_KW"].sum()), 2)
    peak_idx = combined_df["PREDICTED_EXPORT_KW"].idxmax()
    peak_export_kw = round(float(combined_df.loc[peak_idx, "PREDICTED_EXPORT_KW"]), 2)
    peak_time = combined_df.loc[peak_idx, "TIMESTAMP"].strftime("%Y-%m-%d %H:%M")

    # Daily aggregation
    daily_summaries = []
    for date_str, group in combined_df.groupby("DATE", sort=False):
        total_kwh = round(float(group["PREDICTED_EXPORT_KW"].sum()), 2)
        p_idx = group["PREDICTED_EXPORT_KW"].idxmax()
        day_name = group["TIMESTAMP"].iloc[0].strftime("%A")

        daily_summaries.append({
            "date": date_str,
            "day_name": day_name,
            "total_export_kwh": total_kwh,
            "peak_export_kw": round(float(group["PREDICTED_EXPORT_KW"].max()), 2),
            "peak_time": group.loc[p_idx, "TIMESTAMP"].strftime("%H:%M"),
            "avg_export_kw": round(float(group["PREDICTED_EXPORT_KW"].mean()), 2),
            "avg_ghi": round(float(group["GHI"].mean()), 1),
            "max_ghi": round(float(group["GHI"].max()), 1),
            "avg_temp": round(float(group["TEMP"].mean()), 1),
            "avg_cloud": round(float(group["CLOUD"].mean()), 1)
        })

    avg_daily_export_kwh = round(total_7day_export_kwh / len(daily_summaries), 2) if daily_summaries else 0.0

    # Hourly array for API
    hourly_records = []
    for idx, row in combined_df.iterrows():
        hourly_records.append({
            "ds": row["TIMESTAMP"].strftime("%Y-%m-%d %H:%M"),
            "date": row["DATE"],
            "time": row["TIME"],
            "predicted_export_kw": round(float(row["PREDICTED_EXPORT_KW"]), 2),
            "ghi": round(float(row["GHI"]), 1),
            "temp": round(float(row["TEMP"]), 1),
            "cloud": int(row["CLOUD"])
        })

    # Save predictions into past_7_days.json so the next forecast cycle
    # can use them as the historical "past 7 days" input
    _save_predictions_to_past_json(combined_df)

    return {
        "status": "success",
        "total_7day_export_kwh": total_7day_export_kwh,
        "peak_export_kw": peak_export_kw,
        "peak_time": peak_time,
        "avg_daily_export_kwh": avg_daily_export_kwh,
        "max_ghi": round(float(combined_df["GHI"].max()), 1),
        "daily": daily_summaries,
        "hourly": hourly_records
    }


def get_past_solar_data():
    """Retrieve past 7 days historical solar data."""
    past_df = prepare_past_dataframe()
    records = []
    for ts, row in past_df.iterrows():
        records.append({
            "ds": ts.strftime("%Y-%m-%d %H:%M"),
            "date": ts.strftime("%Y-%m-%d"),
            "time": ts.strftime("%H:%M"),
            "export_kw": round(float(row["EXPORT_KW"]), 2),
            "ghi": round(float(row["GHI"]), 1),
            "temp": round(float(row["TEMP"]), 1),
            "cloud": int(row["CLOUD"])
        })
    return {
        "status": "success",
        "records": records,
        "total_past_export_kwh": round(float(past_df["EXPORT_KW"].sum()), 2),
        "peak_past_export_kw": round(float(past_df["EXPORT_KW"].max()), 2)
    }
