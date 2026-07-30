# -*- coding: utf-8 -*-
"""
train_and_export.py
────────────────────
Offline entry point for Module C. Loads the two raw CSVs, runs the trimmed pipeline
(pipeline.py), and writes everything the Flask dashboard needs into artifacts/:

    artifacts/zones.json               map + zone profile payload
    artifacts/module_c_results.json    per-zone forecast / confidence / risk / priority
                                        (includes daily_peaks_kw + risk.headroom_to_*,
                                        the inputs the dashboard's scenario simulator
                                        needs to compute a what-if surge on demand)
    artifacts/validation.json          Stage 8.2/8.3: worst-day replay + historical
                                        validation ("Does the detector actually work?")
    artifacts/zone_models/zone_<n>.json  trained XGBoost booster per zone (kept for
                                          reuse / future live inference — the dashboard
                                          does not load these at request time, since the
                                          forecast is frozen at the dataset's last date)

Run manually whenever the underlying meter data changes:
    python3 train_and_export.py

Optionally edit zone_names.json first (see below) after inspecting the map, then
re-run this script to bake the real zone names into the exported JSON.
"""

import json
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

import pipeline

BASE_DIR      = Path(__file__).parent
INPUTS_DIR    = BASE_DIR / 'data' / 'inputs'
F_HOUSEHOLDS  = INPUTS_DIR / 'house_consumption_summary_for_clustering.csv'
F_HOURLY      = INPUTS_DIR / 'hourly_data_final_cleaned.csv'
ZONE_NAMES_FILE = BASE_DIR / 'zone_names.json'

ARTIFACTS_DIR    = BASE_DIR / 'artifacts'
ZONE_MODELS_DIR  = ARTIFACTS_DIR / 'zone_models'
ZONES_JSON       = ARTIFACTS_DIR / 'zones.json'
RESULTS_JSON     = ARTIFACTS_DIR / 'module_c_results.json'
VALIDATION_JSON  = ARTIFACTS_DIR / 'validation.json'


def load_zone_names() -> dict:
    """
    Optional override file, e.g.:
        { "0": "Nugegoda East", "1": "Nugegoda West", "2": "Nugegoda South" }
    Falls back to generic "Zone {n}" labels if the file doesn't exist yet —
    edit it after checking the exported map, then re-run this script.
    """
    if ZONE_NAMES_FILE.exists():
        with open(ZONE_NAMES_FILE, 'r', encoding='utf-8') as f:
            raw = json.load(f)
        return {int(k): v for k, v in raw.items()}
    return dict(pipeline.DEFAULT_ZONE_NAMES)


def main():
    print(f'[train_and_export] Loading households  <- {F_HOUSEHOLDS}')
    hh = pd.read_csv(F_HOUSEHOLDS)
    print(f'[train_and_export] Loading hourly data  <- {F_HOURLY}')
    hourly = pd.read_csv(F_HOURLY, parse_dates=['TIMESTAMP'])
    hourly = hourly.rename(columns={'AVG._IMPORT_KW (kW)': 'kw'})

    print(f'[train_and_export] {len(hh)} households, {hourly.SERIAL.nunique()} meters, '
          f'{len(hourly):,} hourly rows')

    zone_names = load_zone_names()
    print(f'[train_and_export] Zone names: {zone_names}')

    print('[train_and_export] Running pipeline (clustering, per-zone forecasting, '
          'confidence, risk, priority) — this can take a minute…')
    out = pipeline.run_pipeline(hh, hourly, zone_names=zone_names)

    ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
    ZONE_MODELS_DIR.mkdir(parents=True, exist_ok=True)

    generated_at = datetime.now(timezone.utc).isoformat()

    zones_payload = {
        'generated_at': generated_at,
        'map_center': out['map_center'],
        'zones': out['zones_meta'],
    }
    with open(ZONES_JSON, 'w', encoding='utf-8') as f:
        json.dump(zones_payload, f, indent=2)
    print(f'[train_and_export] Wrote {ZONES_JSON}')

    results_payload = {
        'generated_at': generated_at,
        'study_window': out['study_window'],
        'next_day_date': out['next_day_date'],
        'z_conf': out['z_conf'],
        'zones': out['results'],
    }
    with open(RESULTS_JSON, 'w', encoding='utf-8') as f:
        json.dump(results_payload, f, indent=2)
    print(f'[train_and_export] Wrote {RESULTS_JSON}')

    validation_payload = {
        'generated_at': generated_at,
        'worst_day_replay': out['worst_day_replay'],
        'historical_validation': out['historical_validation'],
    }
    with open(VALIDATION_JSON, 'w', encoding='utf-8') as f:
        json.dump(validation_payload, f, indent=2)
    print(f'[train_and_export] Wrote {VALIDATION_JSON}')

    for zone_id, model in out['models'].items():
        model_path = ZONE_MODELS_DIR / f'zone_{zone_id}.json'
        model.save_model(str(model_path))
        print(f'[train_and_export] Wrote {model_path}')

    print('[train_and_export] Done.')
    top = out['results'][0]
    print(f"[train_and_export] Top priority: {top['name']} (score {top['priority_score']}) "
          f"-> {top['action']}")


if __name__ == '__main__':
    main()
