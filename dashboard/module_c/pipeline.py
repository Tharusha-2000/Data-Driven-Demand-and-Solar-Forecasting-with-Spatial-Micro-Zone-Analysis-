# -*- coding: utf-8 -*-
"""
pipeline.py
───────────
Module C — Micro-Zone Demand Forecasting & Operational Decision Support.

Production-trimmed port of the Module C research notebook. Contains only what the
deployed pipeline actually uses:

  - Stage 1  Clustering (households -> contiguous micro-zones)
  - Stage 2  Zone aggregation + per-zone XGBoost demand model
  - Stage 3  Forecast confidence (relative score + absolute reliability band)
  - Stage 4  Surge early warning + structural trend detection
  - Stage 5  Peak-demand risk ranking
  - Stage 7  Operational priority score + recommended action
  - Stage 8  Detector validation (scenario headroom, worst-day replay, historical
             validation) — the dashboard's "Does the detector actually work?" panel

Deliberately excluded (research-only, do not feed the deployed result):
  - The alpha/K selection sweeps (Stage 1.2/1.3) — alpha and K were already decided
    by the notebook's own analysis; this module just applies the final values.
  - The 4-model comparison in the notebook's Stage 2.2 (SeasonalNaive / LinearRegression
    / RandomForest / XGBoost) — every later notebook stage hardcodes the same XGBoost
    config (`make_model()`) regardless of which model that comparison favoured, so the
    comparison never actually reaches the deployed pipeline. Only that one XGBoost
    config is reproduced here.
  - LSTM comparison and Supporting Studies S1/S2 — research justification for the
    write-up, not something the running dashboard needs.
  - matplotlib / folium / display() / print() narration — replaced by plain return
    values; the dashboard renders its own charts and map from JSON.

Note on Stage 8's "scenario injection" (the +X% what-if surge): the notebook computes
that live from a chosen surge % against a zone's forecast peak, residual spread and
historical daily-peaks distribution — all cheap arithmetic, no re-fitting. Rather than
precompute one fixed surge %, this module exports what that arithmetic needs
(daily_peaks_kw, resid_std, own_P80/P95, z_conf) so the Flask app can run the same
calculation on demand for whatever zone/surge % a dashboard user picks — see
microzone_utils.run_scenario(). Only the worst-day replay and historical validation
(Stage 8.2/8.3, which use fixed real dates, not a user-chosen parameter) are
precomputed here and exported as-is.

This module has no Flask, Colab, or notebook-display dependencies. It is imported by
train_and_export.py (offline) only — the Flask app never imports it.
"""

from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats as sps
from scipy.spatial import ConvexHull
from sklearn.preprocessing import StandardScaler
from sklearn.metrics import mean_squared_error
from xgboost import XGBRegressor

import scoring

# ────────────────────────────────────────────────────────────────
# Configuration — values already justified by the notebook's own
# alpha/K sweeps and model comparison; this module applies them.
# ────────────────────────────────────────────────────────────────
FINAL_ALPHA  = 0.70    # geographic share of the clustering distance metric
FINAL_K      = 3       # number of deployed micro-zones
RANDOM_STATE = 42

MIN_COVERAGE = 0.90    # drop zone-hours where <90% of the zone's meter panel reported
HOLDOUT      = 24 * 14 # trailing 2 weeks held out to measure residual spread
Z_CONF       = 1.645   # one-sided ~95% margin on the residual spread

TREND_WINDOWS = [28, 42, 56]   # trailing windows (days) scanned for a structural trend
SLOPE_MIN_PCT = 0.15           # min |% change per day| to count as a real trend
SAFE_BAND_PCT = 95             # each zone's safe operating band = this pctile of its daily peaks

NRMSE_HIGH = 8.0     # nRMSE% below this -> High reliability
NRMSE_MED  = 12.0    # below this        -> Medium ; above -> Low

DEFAULT_ZONE_NAMES = {i: f'Zone {i}' for i in range(FINAL_K)}


# ══════════════════════════════════════════════════════════════
# Stage 0 · shared feature / model / metric helpers
# ══════════════════════════════════════════════════════════════

def make_features(s: pd.Series) -> pd.DataFrame:
    """Univariate hourly series -> supervised matrix (lags + rolling + calendar)."""
    X = pd.DataFrame(index=s.index)
    X['lag_1']   = s.shift(1)
    X['lag_2']   = s.shift(2)
    X['lag_24']  = s.shift(24)
    X['lag_168'] = s.shift(168)
    X['roll_24']  = s.shift(1).rolling(24).mean()
    X['roll_168'] = s.shift(1).rolling(168).mean()
    h = X.index.hour
    d = X.index.dayofweek
    X['hs'] = np.sin(2 * np.pi * h / 24); X['hc'] = np.cos(2 * np.pi * h / 24)
    X['ds'] = np.sin(2 * np.pi * d / 7);  X['dc'] = np.cos(2 * np.pi * d / 7)
    X['wknd'] = (d >= 5).astype(int)
    X['y'] = s
    return X.dropna()


def feat_cols_of(F: pd.DataFrame) -> list:
    return [c for c in F.columns if c != 'y']


def make_model() -> XGBRegressor:
    """The single model config used for confidence, warning, risk and priority."""
    return XGBRegressor(n_estimators=400, learning_rate=0.05, max_depth=5,
                         subsample=0.9, colsample_bytree=0.9,
                         random_state=RANDOM_STATE, n_jobs=-1)


def nrmse_pct(actual, pred) -> float:
    rmse = np.sqrt(mean_squared_error(actual, pred))
    return 100 * rmse / np.mean(actual)


def daily_peaks(s: pd.Series) -> pd.Series:
    return s.groupby(s.index.normalize()).max()


# ══════════════════════════════════════════════════════════════
# Stage 1 · households -> contiguous micro-zones
# ══════════════════════════════════════════════════════════════

def _kmeans_pp_init(X: np.ndarray, k: int, rng: np.random.Generator) -> np.ndarray:
    """k-means++ seeding: each new centre is chosen with probability proportional
    to its squared distance from the nearest centre already picked."""
    n = X.shape[0]
    centers = np.empty((k, X.shape[1]))
    centers[0] = X[rng.integers(n)]
    closest_sq_dist = ((X - centers[0]) ** 2).sum(axis=1)
    for i in range(1, k):
        probs = closest_sq_dist / closest_sq_dist.sum()
        centers[i] = X[rng.choice(n, p=probs)]
        new_sq_dist = ((X - centers[i]) ** 2).sum(axis=1)
        closest_sq_dist = np.minimum(closest_sq_dist, new_sq_dist)
    return centers


def kmeans_labels(X: np.ndarray, k: int, n_init: int, random_state: int, max_iter: int = 300) -> np.ndarray:
    """
    Plain NumPy Lloyd's-algorithm K-Means with k-means++ seeding and multiple random
    restarts (lowest-inertia restart wins) — a dependency-free stand-in for
    sklearn.cluster.KMeans(init='k-means++', n_init=n_init). Used instead of
    scikit-learn's KMeans because sklearn.cluster's compiled extension is blocked by
    this machine's Windows Application Control policy, while the rest of
    scikit-learn (StandardScaler, metrics) is unaffected.
    """
    rng_master = np.random.default_rng(random_state)
    best_inertia, best_labels = np.inf, None

    for _ in range(n_init):
        rng = np.random.default_rng(rng_master.integers(2 ** 32))
        centers = _kmeans_pp_init(X, k, rng)
        for _ in range(max_iter):
            dists = ((X[:, None, :] - centers[None, :, :]) ** 2).sum(axis=2)
            labels = dists.argmin(axis=1)
            new_centers = np.array([
                X[labels == c].mean(axis=0) if np.any(labels == c) else centers[c]
                for c in range(k)
            ])
            if np.allclose(new_centers, centers):
                centers = new_centers
                break
            centers = new_centers

        dists = ((X[:, None, :] - centers[None, :, :]) ** 2).sum(axis=2)
        labels = dists.argmin(axis=1)
        inertia = float(dists[np.arange(len(X)), labels].sum())
        if inertia < best_inertia:
            best_inertia, best_labels = inertia, labels

    return best_labels


def cluster_households(hh: pd.DataFrame) -> dict:
    """
    Partition households into FINAL_K contiguous micro-zones at FINAL_ALPHA.

    Returns a dict with the labelled household frame, the metre-projected
    coordinates (needed later for convex-hull polygons), and the study-area centre.
    """
    lat0, lon0 = hh.LATITUDE.mean(), hh.LONGITUDE.mean()
    x_m = (hh.LONGITUDE - lon0) * 111320 * np.cos(np.radians(lat0))
    y_m = (hh.LATITUDE - lat0) * 110540
    geo = np.column_stack([x_m, y_m])

    demand_scaler = StandardScaler()
    dem = demand_scaler.fit_transform(hh[['mean_import_kw', 'peak_import_kw']])

    geo_block_scale = np.sqrt(geo.var(axis=0).sum())
    dem_block_scale = np.sqrt(dem.var(axis=0).sum())
    geo_n = geo / geo_block_scale
    dem_n = dem / dem_block_scale

    space = np.hstack([np.sqrt(FINAL_ALPHA) * geo_n, np.sqrt(1 - FINAL_ALPHA) * dem_n])

    labels = kmeans_labels(space, k=FINAL_K, n_init=20, random_state=RANDOM_STATE)

    hh = hh.copy()
    hh['microzone'] = labels

    return {'hh': hh, 'geo': geo, 'lat0': lat0, 'lon0': lon0}


def zone_polygons_and_profile(hh: pd.DataFrame, geo: np.ndarray, zone_names: dict) -> dict:
    """Convex-hull footprint + summary profile per zone."""
    polygons, areas = {}, {}
    for z in sorted(hh.microzone.unique()):
        mask = hh.microzone.values == z
        pts_ll = hh.loc[mask, ['LATITUDE', 'LONGITUDE']].values
        hull = ConvexHull(geo[mask])
        areas[z] = hull.volume / 1e6                      # 2-D "volume" = polygon area (m^2 -> km^2)
        polygons[z] = [[float(p[0]), float(p[1])] for p in pts_ll[hull.vertices]]

    profile = (hh.groupby('microzone')
                 .agg(households=('SERIAL', 'count'),
                      avg_mean_kw=('mean_import_kw', 'mean'),
                      avg_peak_kw=('peak_import_kw', 'mean'))
                 .reset_index())
    profile['area_km2'] = profile.microzone.map(areas).round(3)
    profile['name'] = profile.microzone.map(lambda z: zone_names.get(z, zone_names.get(str(z), f'Zone {z}')))
    profile['centroid'] = profile.microzone.map(
        lambda z: {'lat': float(hh.loc[hh.microzone == z, 'LATITUDE'].mean()),
                   'lon': float(hh.loc[hh.microzone == z, 'LONGITUDE'].mean())}
    )
    profile['polygon'] = profile.microzone.map(polygons)
    return profile.set_index('microzone').to_dict(orient='index')


# ══════════════════════════════════════════════════════════════
# Stage 2 · coverage-robust zone aggregation
# ══════════════════════════════════════════════════════════════

def build_zone_frame(hourly: pd.DataFrame, label_map: dict, min_cov: float = MIN_COVERAGE) -> pd.DataFrame:
    """
    label_map : dict {SERIAL -> zone id}
    Returns hourly MEAN kW PER REPORTING HOUSEHOLD, one column per zone, restricted to
    hours where every zone has adequate meter coverage. Mean-per-household rather than a
    raw sum, because the reporting panel shrinks over time.
    """
    lab = pd.DataFrame({'SERIAL': list(label_map.keys()), 'microzone': list(label_map.values())})
    d = hourly.merge(lab, on='SERIAL', how='inner')

    zh = (d.groupby(['microzone', 'TIMESTAMP'])
            .agg(mean_kw=('kw', 'mean'), n_meters=('SERIAL', 'nunique'))
            .reset_index())

    panel_max = zh.groupby('microzone').n_meters.max()
    zh['coverage'] = zh.n_meters / zh.microzone.map(panel_max)
    zh = zh[zh.coverage >= min_cov].copy()

    frame = zh.pivot(index='TIMESTAMP', columns='microzone', values='mean_kw')
    frame = frame.dropna().asfreq('h')
    frame = frame.interpolate('time', limit=6).dropna()
    return frame


# ══════════════════════════════════════════════════════════════
# Stage 2b · direct next-calendar-day forecast (no recursion)
# ══════════════════════════════════════════════════════════════

def forecast_next_calendar_day(model, feat_cols: list, s: pd.Series) -> pd.Series:
    """
    Every hour of "tomorrow" (the day after s's last timestamp) is predicted
    independently. lag_24 / lag_168 point at real history; the short lags (lag_1,
    lag_2) are filled from the recent same-hour average so errors cannot compound
    across the 24-hour horizon.
    """
    target_start = s.index[-1].normalize() + pd.Timedelta(days=1)
    target_idx = pd.date_range(target_start, periods=24, freq='h')

    recent = s.iloc[-24 * 7:]
    hour_mean = recent.groupby(recent.index.hour).mean()

    rows = {}
    for t in target_idx:
        h, d = t.hour, t.dayofweek

        def hist_at(delta_hours):
            idx = t - pd.Timedelta(hours=delta_hours)
            return s.loc[idx] if idx in s.index else hour_mean.get(idx.hour, s.mean())

        row = {
            'lag_1':   hour_mean.get((h - 1) % 24, s.mean()),
            'lag_2':   hour_mean.get((h - 2) % 24, s.mean()),
            'lag_24':  hist_at(24),
            'lag_168': hist_at(168),
            'roll_24':  s.iloc[-24:].mean(),
            'roll_168': s.iloc[-168:].mean(),
            'hs': np.sin(2 * np.pi * h / 24), 'hc': np.cos(2 * np.pi * h / 24),
            'ds': np.sin(2 * np.pi * d / 7),  'dc': np.cos(2 * np.pi * d / 7),
            'wknd': int(d >= 5),
        }
        rows[t] = float(model.predict(pd.DataFrame([row])[feat_cols])[0])
    return pd.Series(rows)


# ══════════════════════════════════════════════════════════════
# Stages 3-5, 7 · per-zone confidence, warning, trend, risk, priority
# ══════════════════════════════════════════════════════════════

def _structural_trend(s: pd.Series) -> dict:
    """Multi-window trailing slope scan (Stage 4b)."""
    daily = s.groupby(s.index.normalize()).mean()
    mean = daily.mean()

    best = None
    for W in TREND_WINDOWS:
        if W > len(daily):
            continue
        w = daily.iloc[-W:]
        slope, intercept, rval, pval, se = sps.linregress(np.arange(len(w)), w.values)
        slope_pct = 100 * slope / mean
        if pval < 0.05 and abs(slope_pct) >= SLOPE_MIN_PCT:
            if best is None or abs(slope_pct) > abs(best[1]):
                best = (W, slope_pct, pval)

    if best:
        W, slope_pct, pval = best
        trend = 'Rising' if slope_pct > 0 else 'Falling'
    else:
        W = min(TREND_WINDOWS[-1], len(daily))
        w = daily.iloc[-W:]
        slope, intercept, rval, pval, se = sps.linregress(np.arange(len(w)), w.values)
        slope_pct = 100 * slope / mean
        trend = 'None'

    return {
        'best_window_days': int(W),
        'slope_pct_per_day': round(float(slope_pct), 3),
        'change_over_window_pct': round(float(slope_pct * W), 1),
        'p_value': round(float(pval), 4),
        'trend': trend,
    }


def analyze_zones(series: pd.DataFrame, zone_names: dict) -> list:
    """
    Run Stages 3 (confidence), 4 (warning + trend), 5 (risk) and 7 (priority) for
    every zone column in `series`. Returns a list of per-zone result dicts, plus the
    fitted full-history model for each zone (needed by train_and_export.py to persist
    the model artifact).
    """
    zones = list(series.columns)
    raw = {}

    for z in zones:
        s = series[z]
        F = make_features(s)
        fc = feat_cols_of(F)

        # ── holdout fit -> confidence + residual spread ──
        tr, te = F.iloc[:-HOLDOUT], F.iloc[-HOLDOUT:]
        m_holdout = make_model()
        m_holdout.fit(tr[fc], tr['y'])
        pred = m_holdout.predict(te[fc])
        resid_std = float((te['y'].values - pred).std())
        nrmse = nrmse_pct(te['y'], pred)

        # ── full-history fit -> the deployed next-day forecast ──
        m_full = make_model()
        m_full.fit(F[fc], F['y'])
        next_day = forecast_next_calendar_day(m_full, fc, s)

        fc_peak = float(next_day.max())
        peak_hour = int(next_day.idxmax().hour)
        risk_peak = fc_peak + Z_CONF * resid_std

        dp = daily_peaks(s)
        p80, p95 = float(np.percentile(dp, 80)), float(np.percentile(dp, SAFE_BAND_PCT))
        warning = 'YES' if risk_peak > p95 else 'No'
        tier = scoring.risk_tier(risk_peak, p80, p95)
        self_pctile = float(100 * (dp < risk_peak).mean())

        # how much extra surge (%) this zone's forecast peak could absorb before
        # crossing into Medium / High — same arithmetic the scenario simulator uses
        headroom_med  = max(0.0, (p80 - Z_CONF * resid_std) / fc_peak - 1) * 100
        headroom_high = max(0.0, (p95 - Z_CONF * resid_std) / fc_peak - 1) * 100

        trend_info = _structural_trend(s)

        raw[z] = {
            'zone': int(z),
            'name': zone_names.get(z, zone_names.get(str(z), f'Zone {z}')),
            'nrmse_pct': round(nrmse, 2),
            'resid_std': round(resid_std, 4),
            'forecast': {
                'dates': [t.strftime('%Y-%m-%d %H:%M') for t in next_day.index],
                'yhat': [round(v, 3) for v in next_day.values.tolist()],
                'peak_kw': round(fc_peak, 3),
                'peak_hour': peak_hour,
            },
            'risk': {
                'risk_adj_peak_kw': round(risk_peak, 3),
                'own_P80': round(p80, 3),
                'own_P95': round(p95, 3),
                'self_pctile': round(self_pctile, 1),
                'risk_tier': tier,
                'headroom_to_medium_pct': round(headroom_med, 1),
                'headroom_to_high_pct': round(headroom_high, 1),
            },
            'warning': {
                'safe_band_P95': round(p95, 3),
                'over_band_pct': round(100 * (risk_peak - p95) / p95, 1),
                'WARNING': warning,
            },
            'trend': trend_info,
            'daily_peaks_kw': [
                {'date': d.date().isoformat(), 'peak_kw': round(float(v), 3)}
                for d, v in dp.items()
            ],
            '_model': m_full,
        }

    # ── relative confidence 0-100 across this zone set ──
    nrmse_vals = [r['nrmse_pct'] for r in raw.values()]
    lo, hi = min(nrmse_vals), max(nrmse_vals)
    rng = (hi - lo) or 1.0

    def band(e):
        return 'High' if e < NRMSE_HIGH else ('Medium' if e < NRMSE_MED else 'Low')

    for z, r in raw.items():
        r['confidence'] = round(100 * (1 - (r['nrmse_pct'] - lo) / rng), 1)
        r['reliability'] = band(r['nrmse_pct'])

    # ── priority score + recommended action (Stage 7) ──
    for z, r in raw.items():
        r['priority_score'] = scoring.priority_score(
            r['warning']['over_band_pct'], r['risk']['risk_tier'],
            r['risk']['self_pctile'], r['trend']['trend'], r['trend']['slope_pct_per_day'])
        r['action'] = scoring.recommend(r['warning']['WARNING'], r['risk']['risk_tier'],
                                         r['trend']['trend'], r['reliability'])

    ordered = sorted(raw.values(), key=lambda r: r['priority_score'], reverse=True)
    for i, r in enumerate(ordered, start=1):
        r['rank'] = i

    return ordered


# ══════════════════════════════════════════════════════════════
# Stage 8 · does the detector actually work?
# (worst-day replay + historical validation — real data only, no injected numbers;
#  the scenario-injection what-if is computed on demand, see module docstring)
# ══════════════════════════════════════════════════════════════

def worst_day_replay(series: pd.DataFrame, results_by_zone: dict) -> list:
    """
    Stage 8.2 — if each zone repeated its own worst OBSERVED day, how would it rank?
    Uses only real historical peaks, no injected numbers.
    """
    out = []
    for z, r in results_by_zone.items():
        s = series[z]
        dp = daily_peaks(s)
        worst_peak = float(dp.max())
        worst_day = dp.idxmax().date().isoformat()
        risk_peak = worst_peak + Z_CONF * r['resid_std']
        p80, p95 = r['risk']['own_P80'], r['risk']['own_P95']
        tier = scoring.risk_tier(risk_peak, p80, p95)
        out.append({
            'zone': int(z), 'name': r['name'],
            'worst_observed_peak_kw': round(worst_peak, 3),
            'worst_day': worst_day,
            'risk_adj_peak_if_repeated_kw': round(risk_peak, 3),
            'tier_if_repeated': tier,
        })
    out.sort(key=lambda x: x['risk_adj_peak_if_repeated_kw'], reverse=True)
    return out


def historical_validation(series: pd.DataFrame, results_by_zone: dict) -> dict:
    """
    Stage 8.3 — point the assessment at a real past high-demand day (the system-wide
    peak day in the study window) and check whether the zone that actually recorded
    the largest peak that day is the one the ranking would have flagged.
    """
    total = series.mean(axis=1)
    sys_daily = total.groupby(total.index.normalize()).max()
    validation_date = sys_daily.idxmax().normalize()

    rows = []
    for z, r in results_by_zone.items():
        s = series[z]
        day = s[s.index.normalize() == validation_date]
        if len(day) == 0:
            continue
        actual_peak = float(day.max())
        actual_hour = int(day.idxmax().hour)
        others = daily_peaks(s[s.index.normalize() != validation_date])
        pct = float(100 * (others < actual_peak).mean())
        tier = scoring.risk_tier(actual_peak, float(np.percentile(others, 80)),
                                  float(np.percentile(others, 95)))
        rows.append({
            'zone': int(z), 'name': r['name'],
            'actual_peak_kw': round(actual_peak, 3),
            'peak_hour': actual_hour,
            'pctile_vs_own_history': round(pct, 1),
            'tier_that_day': tier,
        })
    rows.sort(key=lambda x: x['actual_peak_kw'], reverse=True)

    return {
        'validation_date': validation_date.date().isoformat(),
        'zones': rows,
        'top_zone': rows[0]['zone'] if rows else None,
    }


def run_pipeline(hh: pd.DataFrame, hourly: pd.DataFrame, zone_names: dict = None) -> dict:
    """
    Full production pipeline: households + hourly meter data -> everything the
    dashboard needs. Returns a dict with 'zones_meta' (map/profile payload),
    'results' (per-zone analysis payload, models stripped out for JSON export)
    and 'models' ({zone_id: fitted XGBRegressor}, for train_and_export.py to persist).
    """
    zone_names = zone_names or DEFAULT_ZONE_NAMES

    clustered = cluster_households(hh)
    hh_labelled, geo, lat0, lon0 = clustered['hh'], clustered['geo'], clustered['lat0'], clustered['lon0']

    profile = zone_polygons_and_profile(hh_labelled, geo, zone_names)

    label_map = dict(zip(hh_labelled.SERIAL, hh_labelled.microzone))
    series = build_zone_frame(hourly, label_map)

    analyzed = analyze_zones(series, zone_names)

    models = {r['zone']: r.pop('_model') for r in analyzed}
    results_by_zone = {r['zone']: r for r in analyzed}

    replay = worst_day_replay(series, results_by_zone)
    validation = historical_validation(series, results_by_zone)

    zones_meta = []
    for z, prof in profile.items():
        zones_meta.append({
            'zone': int(z),
            'name': prof['name'],
            'households': int(prof['households']),
            'area_km2': float(prof['area_km2']),
            'avg_mean_kw': round(float(prof['avg_mean_kw']), 3),
            'avg_peak_kw': round(float(prof['avg_peak_kw']), 3),
            'centroid': prof['centroid'],
            'polygon': prof['polygon'],
        })
    zones_meta.sort(key=lambda r: r['zone'])

    last_day = series.index[-1].normalize()

    return {
        'map_center': {'lat': float(lat0), 'lon': float(lon0)},
        'study_window': {
            'start': series.index.min().strftime('%Y-%m-%d'),
            'end': series.index.max().strftime('%Y-%m-%d'),
        },
        'next_day_date': (pd.Timestamp(last_day) + pd.Timedelta(days=1)).date().isoformat(),
        'z_conf': Z_CONF,
        'zones_meta': zones_meta,
        'results': analyzed,
        'worst_day_replay': replay,
        'historical_validation': validation,
        'models': models,
    }
