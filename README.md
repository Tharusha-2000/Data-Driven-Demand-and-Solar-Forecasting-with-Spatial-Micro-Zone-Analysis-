# Data-Driven Demand and Solar Forecasting with Spatial Micro-Zone Analysis

A web application and machine learning dashboard for 24-hour electricity demand forecasting, solar generation analysis, and spatial micro-zone insights for Nugegoda, Sri Lanka. Powered by **Flask**, **Meta Prophet**, **Chart.js**, and **Open-Meteo Weather API**.

---

## 🌟 Key Features

- **Electricity Demand Forecasting**: 24-hour ahead hourly electricity load predictions powered by a trained Prophet model with custom daily & weekly seasonalities and temperature regressors.
- **Automated Weather Syncing**: Automatically fetches 7-day hourly weather data (Temperature, Cloud Cover, Solar Radiation, Precipitation, Wind) from Open-Meteo for Nugegoda (`6.9389° N, 79.8542° E`).
- **Data-Driven Feature Matrix**: Automated background scheduler syncs weather and calendar flags at 01:00 AM daily to construct feature matrices for predictions.
- **Interactive UI Dashboard**: Multi-page Single Page Application (SPA) with a modern dark theme, dual-axis Chart.js visualizations, KPI indicator cards, and hourly breakdowns.
- **RESTful API**: Clean Flask JSON endpoints for automated and custom forecasting requests.

---

## 📋 Prerequisites

Before running the application, ensure you have the following installed:
- **Python**: `3.9` or higher (3.9, 3.10, or 3.11 recommended)
- **pip**: Python package installer
- **Git**

---

## ⚙️ Installation & Setup Guide

### 1. Clone the Repository

```bash
git clone https://github.com/Tharusha-2000/Data-Driven-Demand-and-Solar-Forecasting-with-Spatial-Micro-Zone-Analysis-.git
cd Data-Driven-Demand-and-Solar-Forecasting-with-Spatial-Micro-Zone-Analysis-
```

### 2. Navigate to the Dashboard Directory

```bash
cd dashboard
```

### 3. Create & Activate a Virtual Environment

- **macOS / Linux:**
  ```bash
  python3 -m venv venv
  source venv/bin/activate
  ```

- **Windows (Command Prompt):**
  ```cmd
  python -m venv venv
  venv\Scripts\activate
  ```

- **Windows (PowerShell):**
  ```powershell
  python -m venv venv
  .\venv\Scripts\Activate.ps1
  ```

### 4. Install Dependencies

Install all required Python packages:

```bash
pip install --upgrade pip
pip install -r requirements.txt
```

> **Note:** Key dependencies include `flask`, `prophet`, `pandas`, `numpy`, `requests`, `apscheduler`, and `pytz`.

---

## 🚀 Running the Application

Start the Flask development server:

```bash
python3 app.py
```

Upon launching, the app will automatically:
1. Load the Prophet model (`electricityDemandModel.json`).
2. Start the background scheduler for nightly weather & forecast matrix syncs.
3. Perform an initial weather sync & feature matrix build.

Open your browser and navigate to:
```text
http://localhost:5071
```

---

## 📁 Project Structure

```text
Data-Driven-Demand-and-Solar-Forecasting-with-Spatial-Micro-Zone-Analysis-/
├── electricityDemandModel.json   # Pre-trained Prophet ML model parameters
└── dashboard/
    ├── app.py                     # Main Flask backend server & API routes
    ├── model_utils.py             # Model loading & inference utility functions
    ├── weather_sync.py            # Open-Meteo weather API integration script
    ├── forecast_builder.py        # Weather + calendar feature matrix generator
    ├── calendar_2026.json         # Sri Lanka 2026 public holiday & calendar data
    ├── weather_next_7_days.json   # Cached 7-day weather forecast dataset
    ├── forecast_input_7days.json # Pre-built 7-day Prophet feature input matrix
    ├── requirements.txt           # Python dependencies list
    ├── static/                    # Dashboard UI static assets
    │   ├── css/                   # Vanilla CSS styles & theme
    │   └── js/                    # Client-side JavaScript (dashboard.js, Chart.js)
    └── templates/
        └── index.html             # Main dashboard UI template (SPA)
```

---

## 🔌 API Endpoints Reference

| Endpoint | Method | Description |
| :--- | :--- | :--- |
| `GET /` | `GET` | Renders the main Web UI dashboard |
| `POST /api/forecast/single` | `POST` | Generates a 24-hour demand prediction. Optional JSON body: `{"date": "YYYY-MM-DD"}` |
| `GET /api/weather` | `GET` | Retrieves the latest cached 7-day weather forecast |
| `POST /api/weather/sync` | `POST` | Triggers an immediate weather data refresh from Open-Meteo |
| `GET /api/forecast-input` | `GET` | Retrieves the pre-built Prophet feature matrix |
| `POST /api/forecast-input/build` | `POST` | Forces a rebuild of the 7-day feature matrix |

### Sample Request: 24-Hour Demand Forecast

```bash
curl -X POST http://localhost:5071/api/forecast/single \
  -H "Content-Type: application/json" \
  -d '{"date": "2026-07-24"}'
```

---

## 🔧 Troubleshooting

### Port 5071 is already in use
If you see `Address already in use` error when starting `app.py`:

- **macOS / Linux:**
  ```bash
  lsof -i :5071
  kill -9 <PID>
  ```
- **Windows:**
  ```cmd
  netstat -ano | findstr :5071
  taskkill /PID <PID> /F
  ```

### Prophet Installation Errors
If installing `prophet` fails on C++ compiler setup:
- Make sure `cmdstanpy` or `c++` compiler toolchains are installed, or install pre-built wheels:
  ```bash
  pip install prophet --only-binary :all:
  ```

---

## 📜 License

This project is created for Data-Driven Demand and Solar Forecasting with Spatial Micro-Zone Analysis research.
