from __future__ import annotations

import logging
from statistics import median

try:
    from . import config as cfg
except ImportError:  # Allows running via python src/main.py
    import config as cfg

LOGGER = logging.getLogger(__name__)


# Loughran and McDonald (2011, Journal of Finance; 2016, Journal of Accounting
# Research) motivate count-then-normalize finance text measures: classify
# relevant tone/evidence categories, normalize by document length, then compute
# downstream measures outside the classifier.
COUNT_FIELDNAMES = [
    "sentences_total",
    "sentences_concrete",
    "sentences_vague",
    "promotional_terms",
    "buzzword_hedge_terms",
    "distinct_figures",
]


def normalized_census(census: dict[str, int]) -> dict[str, int]:
    """Return a copy containing exactly the expected count fields."""
    return {field: int(census[field]) for field in COUNT_FIELDNAMES}


def score_breakdown(census: dict[str, int]) -> dict[str, float]:
    """Return normalized components used by the deterministic score.

    This implements the count-then-normalize methodology from Loughran and
    McDonald (2011, 2016): classify finance-relevant tone/evidence categories,
    then normalize by document length before scoring.
    """
    c = normalized_census(census)
    n = max(c["sentences_total"], 1)
    return {
        "vague_frac": c["sentences_vague"] / n,
        "promo_density": (c["promotional_terms"] + c["buzzword_hedge_terms"]) / n,
        "figure_density": c["distinct_figures"] / n,
    }


def vagueness_score(census: dict[str, int], weights: dict[str, float] | None = None) -> float:
    """Compute the final 0-100 hype/vagueness score from count census fields."""
    w = cfg.WEIGHTS if weights is None else weights
    breakdown = score_breakdown(census)
    base = 100.0 * breakdown["vague_frac"]
    base += 100.0 * min(breakdown["promo_density"], 1.0) * w["promo"]
    base -= 100.0 * min(breakdown["figure_density"], 1.0) * w["figure"]
    return round(max(0.0, min(100.0, base)), 1)


WEIGHTS = {
    "vague": 0.30,
    "concrete_gap": 0.20,
    "promotional": 0.20,
    "hedging": 0.15,
    "figure_sparsity": 0.15,
}


def vagueness_score_v2(
    census: dict[str, int],
    kappa: dict[str, float],
    weights: dict[str, float] = WEIGHTS,
) -> float:
    """Compute the convex-combination hype/vagueness score without final clipping."""
    T = census["sentences_total"]
    C = census["sentences_concrete"]
    V = census["sentences_vague"]
    P = census["promotional_terms"]
    H = census["buzzword_hedge_terms"]
    F = census["distinct_figures"]

    N = max(T, 1)
    kp, kh, kf = kappa["promotional"], kappa["hedging"], kappa["figure"]

    raw_x1 = V / N
    raw_x2 = 1.0 - C / N
    x1 = min(max(raw_x1, 0.0), 1.0)  # vague-claim prevalence
    x2 = min(max(raw_x2, 0.0), 1.0)  # concreteness gap
    if raw_x1 != x1 or raw_x2 != x2:
        LOGGER.warning("vagueness_score_v2 received inconsistent sentence counts: %s", census)
    x3 = P / (P + kp * N) if (P + kp * N) > 0 else 0.0  # promotional
    x4 = H / (H + kh * N) if (H + kh * N) > 0 else 0.0  # hedging
    x5 = kf / (F + kf) if (F + kf) > 0 else 0.0  # figure sparsity

    s = 100.0 * (
        weights["vague"] * x1
        + weights["concrete_gap"] * x2
        + weights["promotional"] * x3
        + weights["hedging"] * x4
        + weights["figure_sparsity"] * x5
    )
    return round(s, 1)


def estimate_kappa(censuses: list[dict[str, int]]) -> dict[str, float]:
    """Estimate global half-saturation constants from a full scored corpus."""
    p_rates, h_rates, figs = [], [], []
    for c in censuses:
        N = max(c["sentences_total"], 1)
        p_rates.append(c["promotional_terms"] / N)
        h_rates.append(c["buzzword_hedge_terms"] / N)
        figs.append(c["distinct_figures"])
    return {"promotional": median(p_rates), "hedging": median(h_rates), "figure": median(figs)}


def score_reasoning(census: dict[str, int]) -> str:
    """Build a short audit sentence from the normalized score components."""
    c = normalized_census(census)
    breakdown = score_breakdown(c)
    tone_terms = c["promotional_terms"] + c["buzzword_hedge_terms"]
    return (
        f"{breakdown['vague_frac']:.0%} of substantive sentences lack hard figures; "
        f"{c['distinct_figures']} distinct numbers and {tone_terms} promotional or hedge terms drive the score."
    )
