# LLM Validation Pass on Ambiguous SE Rows

This folder documents a scoped, targeted use of an LLM to validate the
deterministic `hype_v3` pipeline (see [`../README.md`](../README.md) for the
full methodology) on the specific subset of filings where the deterministic
SE (Selective Emphasis) signals genuinely conflict with each other. It is a
robustness/validation check, not a replacement for the deterministic
pipeline, and not a re-scoring of the full 14,351-row dataset.

## Why this exists

`khashi_scorer.py`'s SE component was originally designed as a two-stage
process:

1. Python D1-D6 signal extraction (deterministic, described in the main README)
2. An LLM synthesising those six signals into the final SE score + a short
   reasoning, intended to add contextual judgment on top of the raw counts.

Stage 2 was disabled for the full 14,351-row run purely for throughput —
running a local model (qwen3:8b via Ollama) at 60-80 seconds per row would
have taken 5+ days across the full dataset. The result was a pipeline that
is 100% deterministic end to end, with zero actual LLM/agent judgment
anywhere, despite being designed for it. The same was true of CL's guidance
sentence classification, also originally intended to use an LLM for
ambiguous cases and in practice done entirely by a verb list and a regex.

This validation pass re-adds that judgment call, but scoped so it's cheap
and fast rather than a second full run of everything.

## How the ambiguous subset was chosen

Rather than reviewing all 14,351 filings, only rows where the SE sub-signals
genuinely disagree with each other get an LLM call. Specifically: strong
promotion signals (D1 position + D2 density, combined ≥ 4 out of a possible
10) paired with weak concealment signals (D4 burial + D5 demotion, combined
≤ 1), or the exact reverse. This is a real conflict in what the SE score
should represent, not an arbitrary cut.

An earlier, simpler idea, "flag anything in the middle of the score
distribution", was tested and rejected: it captured 12,312 of 14,351 rows
(86%), which is not a useful filter, SE scores are naturally bell-shaped
around the genre average, so "middle of the distribution" is nearly
everything. The signal-conflict rule instead identified **1,067 rows
(7.4% of the dataset)** as genuinely ambiguous, exactly the cases a
deterministic keyword formula can't cleanly resolve and where a contextual
judgment call adds real value instead of decorating a formula that already
works fine on the other 92.6%.

## Implementation

- **Model:** `llama-3.1-8b-instant`, hosted on Groq (fast inference of an
  open-weight model; not a locally-run model, chosen specifically to make a
  ~1,067-row pass finish in minutes rather than days).
- **Prompt:** reuses the exact signal framing from `khashi_scorer.py`'s
  original (dormant) Stage-2 SE prompt, adapted to the fields actually
  exported in the full run's `diag_se_*` columns (non-GAAP share, burial
  percentage, demotion hits, headline hits, positive-fact vs external-blame
  counts, and the D1-D6 sub-scores). No raw filing text is sent, only the
  already-extracted numeric/structural signals, consistent with the
  "Python signals are transparent, LLM adds interpretation on top" design
  in the main pipeline.
- **Output:** structured JSON, `{"se_score": int 0-25, "se_reasoning": str}`,
  validated on receipt (range-checked, retried on malformed output).
- **Concurrency & reliability:** run with a small thread pool (concurrency
  3-8 depending on the pass), retry with exponential backoff on rate limits,
  and a checkpoint written every 20 rows so an interruption never loses more
  than a few rows of progress. Independent script (`khashi_llm_refine.py`
  in `khashi/`), does not import or modify `khashi_scorer.py` or its output.
- **Practical constraints encountered:** the hosted API sits behind
  Cloudflare, which initially blocked Python's default HTTP client
  signature (fixed with a standard browser User-Agent header), and the
  free tier's daily token quota was hit partway through and required
  finishing the batch after the quota reset. Both are documented here as
  honest limitations of relying on a free hosted inference tier for this
  kind of pass, not limitations of the method itself.

## Results

1,064 of 1,067 flagged rows were successfully scored (99.7% completion; 3
rows failed transient rate limits and were not retried further, negligible
at this sample size).

| Metric | Value |
|---|---|
| Correlation (deterministic SE vs. LLM SE) | 0.75 |
| Mean deterministic SE | 13.94 |
| Mean LLM SE | 14.09 |
| Mean signed difference (LLM − deterministic) | +0.15 |
| Agreement within 2 points | 66.0% |
| Agreement within 3 points | 89.0% |
| Agreement within 5 points | 99.9% |
| Max absolute disagreement | 6 points (out of 25) |

The near-zero mean signed difference (+0.15) is the important number here:
the LLM is not systematically inflating or deflating scores relative to the
deterministic formula, it mostly confirms it. That's the result you want
from a robustness check, agreement, not just correlation.

## Key finding: a specific, systematic blind spot

The disagreements are not random noise. Tracing the largest recurring gap
(deterministic = 9, LLM = 14, a consistent +5 jump, occurring 14 times
across HAL, MSI, FCX, AEE, DTE, HCA, and SNA across four separate quarters)
back to the underlying D1-D6 signals shows that **11 of those 14 cases
share the exact same signal signature**: D1=1, D2=3, D3=0, D4=1, D5=0, D6=1
— moderate non-GAAP density, weak headline capture, low burial, and zero
demotion phrases.

Every time this specific combination appears, the deterministic formula
lands at SE=9 (within the 9-13 genre-average band) while the LLM
independently and consistently lands at SE=14 (its "non-GAAP dominates,
negatives buried or minimised" band). This is a genuine, reproducible,
narrow blind spot: filings that promote non-GAAP metrics moderately while
burying negative results *quietly*, without an obvious headline non-GAAP
push or explicit demotion language, are undercounted by the deterministic
formula and consistently flagged as more concealment-heavy by contextual
reading. This is exactly the kind of finding a scoped LLM validation pass
is supposed to surface, and it's a defensible, citable limitation to name
directly in the paper rather than something to discover in Q&A.

## Files in this folder

| File | Description |
|---|---|
| `khashi_hype_llm_refined.csv` | The 1,064 ambiguous rows only, with `se_score` (deterministic), `se_score_llm`, `se_reasoning_llm`, and `abs_diff` columns. |
| `khashi_hype_full_with_llm_refinement.csv` | The full 14,351-row dataset (same as `khashi_hype_full_14351.csv`) with three additional columns merged in: `se_score_llm`, `se_reasoning_llm`, `se_llm_abs_diff`, and `se_llm_reviewed` (boolean — True for the 1,064 rows reviewed, False/blank for the rest). Kept separate from the original full output so nothing about the primary deterministic run is overwritten or ambiguous. |

## Limitations

This is a validation pass, not a rescoring. 92.6% of the dataset has no
LLM signal at all, by design, the deterministic formula was already
working there. The LLM component itself used a single free-tier hosted
model rather than an ensemble or a paid, higher-throughput tier, results
should be read as "one contextual model's judgment on the ambiguous
subset," not a ground truth. Extending this to a larger share of the
dataset, or reviewing the identified blind-spot signature specifically,
is a natural next step and is noted as future work in the main
[`README.md`](../README.md).

## Reproducing this

The script is `khashi_llm_refine.py` in `khashi/` (one level up from this
folder), fully independent of `khashi_scorer.py`. See the docstring at the
top of that file for setup (Groq API key via a local `.env`, not committed)
and run instructions.
