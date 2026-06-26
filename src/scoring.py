from __future__ import annotations

try:
    from . import config as cfg
except ImportError:  # Allows running via python src/main.py
    import config as cfg


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


def score_reasoning(census: dict[str, int]) -> str:
    """Build a short audit sentence from the normalized score components."""
    c = normalized_census(census)
    breakdown = score_breakdown(c)
    tone_terms = c["promotional_terms"] + c["buzzword_hedge_terms"]
    return (
        f"{breakdown['vague_frac']:.0%} of substantive sentences lack hard figures; "
        f"{c['distinct_figures']} distinct numbers and {tone_terms} promotional or hedge terms drive the score."
    )
