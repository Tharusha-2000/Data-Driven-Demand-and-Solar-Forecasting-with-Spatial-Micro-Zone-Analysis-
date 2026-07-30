"""
microzone_utils.py
───────────────────
Read-only access to Module C's precomputed micro-zone artifacts
(module_c/artifacts/zones.json + module_c_results.json) for the Flask routes.

Mirrors the existing weather/forecast-input pattern in app.py: the JSON is read fresh
from disk on every call (cheap — a few KB) rather than cached in memory, so a manual
pipeline rebuild takes effect immediately without restarting Flask.

Unlike model_utils.load_model(), this module never loads or predicts with the trained
XGBoost models at runtime — Module C's next-day forecast is frozen at the training
dataset's last date (no live smart-meter feed exists to re-forecast against), so the
whole payload is precomputed offline by module_c/train_and_export.py. Rebuilding it
requires the training-only dependencies in module_c/requirements-train.txt, which are
deliberately NOT part of this app's requirements.txt — see rebuild_pipeline() below.

The scenario simulator (run_scenario / run_scenario_multi) is the one exception to
"everything precomputed": it recomputes risk tier + priority score live for a
user-chosen what-if surge. That's safe to do at request time because it only needs
module_c/scoring.py — pure-Python arithmetic with no pandas/numpy/sklearn/xgboost
import, so it can't pull those heavy deps into the Flask process.
"""

import json
import subprocess
import sys
from pathlib import Path

from module_c import scoring

BASE_DIR        = Path(__file__).parent
MODULE_C_DIR    = BASE_DIR / "module_c"
ARTIFACTS_DIR   = MODULE_C_DIR / "artifacts"
ZONES_JSON      = ARTIFACTS_DIR / "zones.json"
RESULTS_JSON    = ARTIFACTS_DIR / "module_c_results.json"
VALIDATION_JSON = ARTIFACTS_DIR / "validation.json"


class MicrozoneArtifactsMissing(RuntimeError):
    """Raised when the artifacts haven't been generated yet."""


def _load_json(path: Path) -> dict:
    if not path.exists():
        raise MicrozoneArtifactsMissing(
            f"{path.name} not found. Run `python module_c/train_and_export.py` "
            f"(with module_c/requirements-train.txt installed) to generate it."
        )
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def get_zones() -> dict:
    """Zone metadata + polygons + centroids, for the Leaflet map."""
    return _load_json(ZONES_JSON)


def get_overview() -> dict:
    """Full per-zone analysis: forecast, confidence, risk, warning, trend, priority."""
    return _load_json(RESULTS_JSON)


def get_validation() -> dict:
    """Stage 8.2/8.3: worst-day replay + historical validation ('does the detector
    actually work?'), precomputed offline since both use fixed real dates."""
    return _load_json(VALIDATION_JSON)


def run_scenario(zone_id: int, surge_pct: float) -> dict | None:
    """
    Stage 8.1 — the scenario-injection what-if: "what if this zone's forecast peak
    were surge_pct% higher?" Reproduces the notebook's Section 8.1 arithmetic exactly
    (surged_peak = forecast_peak * (1 + surge_pct/100); risk_peak = surged_peak +
    z_conf * resid_std; tier/percentile against the zone's own historical daily
    peaks) using only numbers already exported by train_and_export.py — no model
    re-fit, so no xgboost/sklearn needed here. Returns None if zone_id doesn't exist.
    """
    results = get_overview()
    zone = next((z for z in results["zones"] if z["zone"] == zone_id), None)
    if zone is None:
        return None

    z_conf = results["z_conf"]
    base_peak = zone["forecast"]["peak_kw"]
    resid_std = zone["resid_std"]
    p80, p95 = zone["risk"]["own_P80"], zone["risk"]["own_P95"]
    daily_peaks = [d["peak_kw"] for d in zone["daily_peaks_kw"]]

    def assess(peak: float) -> dict:
        risk_peak = peak + z_conf * resid_std
        pctile = 100 * (sum(1 for v in daily_peaks if v < risk_peak) / len(daily_peaks))
        tier = scoring.risk_tier(risk_peak, p80, p95)
        return {
            "forecast_peak_kw": round(peak, 3),
            "risk_adj_peak_kw": round(risk_peak, 3),
            "self_pctile": round(pctile, 1),
            "risk_tier": tier,
        }

    surged_peak = base_peak * (1 + surge_pct / 100)

    return {
        "zone": zone_id,
        "name": zone["name"],
        "surge_pct": surge_pct,
        "base": assess(base_peak),
        "scenario": assess(surged_peak),
    }


def run_scenario_multi(surges: dict) -> dict:
    """
    Like run_scenario(), but applies a (possibly different) surge % to every zone at
    once — so a dashboard user can inject a surge into several zones simultaneously
    and see how the whole priority ranking (not just one zone's tier) responds.

    surges : {zone_id: surge_pct}. A zone missing from this dict is left at 0%
             (i.e. its base forecast, unchanged) so all zones can still be shown
             on the scenario map/table together.

    Trend and forecast reliability are NOT recomputed — an injected surge doesn't
    change a zone's historical trend or how trustworthy its model is, only its
    hypothetical peak — so priority_score/action reuse the zone's real trend and
    reliability, exactly like the notebook's Section 8.1 scenario.
    """
    results = get_overview()
    z_conf = results["z_conf"]
    out_zones = []

    for zone in results["zones"]:
        zone_id = zone["zone"]
        surge_pct = float(surges.get(zone_id, surges.get(str(zone_id), 0)) or 0)

        base_peak = zone["forecast"]["peak_kw"]
        resid_std = zone["resid_std"]
        p80, p95 = zone["risk"]["own_P80"], zone["risk"]["own_P95"]
        daily_peaks = [d["peak_kw"] for d in zone["daily_peaks_kw"]]
        trend, slope = zone["trend"]["trend"], zone["trend"]["slope_pct_per_day"]
        reliability = zone["reliability"]

        surged_peak = base_peak * (1 + surge_pct / 100)
        risk_peak = surged_peak + z_conf * resid_std
        self_pctile = 100 * (sum(1 for v in daily_peaks if v < risk_peak) / len(daily_peaks))
        tier = scoring.risk_tier(risk_peak, p80, p95)
        warning = "YES" if risk_peak > p95 else "No"
        over_band_pct = 100 * (risk_peak - p95) / p95

        score = scoring.priority_score(over_band_pct, tier, self_pctile, trend, slope)
        action = scoring.recommend(warning, tier, trend, reliability)

        out_zones.append({
            "zone": zone_id,
            "name": zone["name"],
            "surge_pct": surge_pct,
            "forecast_peak_kw": round(surged_peak, 3),
            "peak_hour": zone["forecast"]["peak_hour"],
            "risk_adj_peak_kw": round(risk_peak, 3),
            "self_pctile": round(self_pctile, 1),
            "risk_tier": tier,
            "over_band_pct": round(over_band_pct, 1),
            "warning": warning,
            "priority_score": score,
            "action": action,
        })

    out_zones.sort(key=lambda z: z["priority_score"], reverse=True)
    for i, z in enumerate(out_zones, start=1):
        z["rank"] = i

    return {"zones": out_zones}


def get_zone_detail(zone_id: int) -> dict | None:
    """Merged zone metadata + analysis for a single zone, or None if it doesn't exist."""
    zones = {z["zone"]: z for z in get_zones()["zones"]}
    results = {z["zone"]: z for z in get_overview()["zones"]}

    if zone_id not in zones or zone_id not in results:
        return None

    return {**zones[zone_id], **results[zone_id]}


def rebuild_pipeline() -> dict:
    """
    Manually re-run module_c/train_and_export.py to refresh the artifacts, e.g. after
    the underlying meter CSVs change. Requires module_c/requirements-train.txt to be
    installed in this Python environment — runs as a subprocess of the same
    interpreter so a missing dependency fails with a clear message instead of
    crashing the running Flask process.
    """
    proc = subprocess.run(
        [sys.executable, "train_and_export.py"],
        cwd=str(MODULE_C_DIR),
        capture_output=True,
        text=True,
        timeout=900,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"Pipeline rebuild failed:\n{proc.stderr[-4000:]}")
    return {"status": "success", "log": proc.stdout[-4000:]}
