# -*- coding: utf-8 -*-
"""
scoring.py
──────────
Pure-Python, dependency-free (no pandas/numpy/sklearn/xgboost) risk-tier, priority-
score and recommendation formulas — Stages 5 and 7 of the Module C notebook.

Shared between module_c/pipeline.py (offline training) and dashboard/microzone_utils.py
(Flask runtime, for the live multi-zone scenario simulator) so the "what tier is this?"
and "what priority score is this?" logic can never drift between the precomputed
pipeline and an on-demand what-if — both call the exact same functions. Having zero
heavy dependencies is what lets microzone_utils.py import this directly without
pulling xgboost/sklearn into the Flask process.
"""

TIER_BONUS = {'High': 25, 'Medium': 10, 'Low': 0}


def risk_tier(risk_peak: float, p80: float, p95: float) -> str:
    if risk_peak >= p95:
        return 'High'
    if risk_peak >= p80:
        return 'Medium'
    return 'Low'


def priority_score(over_band_pct: float, tier: str, self_pctile: float,
                    trend: str, slope_pct_per_day: float) -> float:
    score = max(0.0, over_band_pct)
    score += TIER_BONUS.get(tier, 0)
    score += self_pctile / 20.0
    if trend == 'Rising':
        score += 5 + max(0.0, slope_pct_per_day) * 5
    return round(score, 1)


def recommend(warning: str, tier: str, trend: str, reliability: str) -> str:
    if warning == 'YES' and trend == 'Rising':
        action = 'IMMEDIATE — inspect feeder, prepare demand response, investigate sustained load growth'
    elif warning == 'YES':
        action = 'PREPARE — stage demand response and monitor through the peak window'
    elif tier == 'High':
        action = 'WATCH — peak is high for this zone; monitor through the peak window'
    elif trend == 'Rising':
        action = 'PLAN — schedule a capacity review for sustained load growth'
    elif trend == 'Falling':
        action = 'REVIEW — declining load; verify meter coverage before assuming a genuine reduction'
    else:
        action = 'ROUTINE — no intervention required'
    if reliability == 'Low':
        action += '  [forecast confidence low — corroborate before acting]'
    return action
