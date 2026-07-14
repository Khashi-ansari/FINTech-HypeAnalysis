"""
Khashi HypeScore Scorer — 4-Component Framework
================================================
Scores SEC 8-K Item 2.02 filings on 4 theoretically-grounded sub-components:
  QO  Qualitative Optimism      (0-25)
  SE  Selective Emphasis        (0-25)
  CL  Certainty Language        (0-25)
  SA  Self-Attribution Bias     (0-25)
  --> HypeScore = QO + SE + CL + SA  (0-100)

Uses same model as Maximilian (qwen3:8b via Ollama) for a clean comparison.
Differences are 100% attributable to the theoretical prompt framework.

Usage:
    python khashi_scorer.py

Requirements:
    - Ollama running locally (ollama serve)
    - qwen3:8b pulled (ollama pull qwen3:8b)
    - Python 3.10+ (standard library only)
"""

from __future__ import annotations

import csv
import json
import os
import random
import time
import urllib.error
import urllib.request
from datetime import datetime

# ─── CONFIGURATION ────────────────────────────────────────────────────────────
INPUT_CSV    = r"C:\Users\khash\OneDrive\Desktop\Agents\item202_clean.csv"
OUTPUT_CSV   = r"C:\Users\khash\Desktop\FINTech_HypeAnalysis\khashi\outputs\khashi_hype_scores.csv"
ERRORS_CSV   = r"C:\Users\khash\Desktop\FINTech_HypeAnalysis\khashi\outputs\khashi_hype_errors.csv"

SAMPLE_SIZE  = 300       # Number of filings to score. Set to 0 for all rows.
RANDOM_SEED  = 42        # For reproducible sampling
MAX_WORDS    = 1500      # Truncate text to first N words (rubric spec)
MIN_WORDS    = 200       # Skip filings shorter than this
MODEL        = "qwen3:8b"
OLLAMA_URL   = "http://localhost:11434"
RETRIES      = 3
TIMEOUT      = 300       # seconds per request
RESUME       = True      # Skip rows already in output CSV
PROGRESS_EVERY = 10      # Print progress every N rows
# ──────────────────────────────────────────────────────────────────────────────

SYSTEM_PROMPT = """You are a precise financial text analyst scoring SEC Form 8-K Item 2.02
earnings-release text on four dimensions of corporate hype.

Score ONLY on the language choices in the text. Do NOT adjust scores based on whether the
company performed well or poorly — strong fundamentals do not justify hype language.

Return ONLY valid JSON matching this exact schema:
{"qo_score": 0, "se_score": 0, "cl_score": 0, "sa_score": 0}

All values must be integers from 0 to 25 inclusive.

SCORING DIMENSIONS:

QO — Qualitative Optimism (0-25):
Measures forward-looking emotional register and positive sentiment beyond what hard numbers justify.
Score HIGH when: text uses superlatives ("record", "exceptional", "outstanding"), vague positive
adjectives ("strong", "robust", "solid"), and emotional enthusiasm without quantitative support.
Score LOW when: claims are grounded in specific figures, neutral tone, balanced reporting.
  0-6   = Minimal: mostly neutral, claims backed by numbers
  7-12  = Moderate: some positive framing, generally substantiated
  13-19 = Elevated: frequent superlatives, enthusiasm beyond what data supports
  20-25 = Extreme: pervasive promotional language, minimal factual grounding

SE — Selective Emphasis (0-25):
Measures asymmetric presentation of GAAP vs. non-GAAP metrics and favourable vs. unfavourable results.
Score HIGH when: non-GAAP metrics are headlined while GAAP figures are buried or absent; positive
segments are expanded while negative ones are minimized or omitted.
Score LOW when: GAAP and non-GAAP figures are presented with equal prominence; reconciliation tables
are clearly co-located; negative results receive proportionate coverage.
  0-6   = Balanced: GAAP prominent, reconciliations accessible, even coverage
  7-12  = Mild: slight preference for non-GAAP, minor omissions
  13-19 = Significant: non-GAAP headlined, GAAP minimized, selective segment coverage
  20-25 = Extreme: GAAP effectively hidden, only favourable metrics discussed

CL — Certainty Language (0-25):
Measures SUPPRESSION of epistemic hedging in forward-looking statements. HIGH score means
the company speaks with unwarranted certainty — omitting words like "may", "could", "might",
"we believe", "approximately", "subject to", "contingent on" — relative to what uncertainty
genuinely exists in the business environment.
IMPORTANT: More hedging words = LOWER score. Suppressed hedging = HIGHER score.
Score HIGH when: forward-looking statements assert outcomes as certainties with no qualifications.
Score LOW when: forward-looking statements appropriately acknowledge uncertainty and risk.
  0-6   = Appropriate hedging: uncertainty language matches genuine business risk
  7-12  = Mild suppression: some overconfident statements, mostly balanced
  13-19 = Significant: forward-looking claims stated as near-certainties
  20-25 = Extreme: zero epistemic hedging, outcomes asserted as guaranteed facts

SA — Self-Attribution Bias (0-25):
Measures asymmetric causal attribution: claiming credit for positives, deflecting blame for negatives.
Score HIGH when: successes attributed to management skill/strategy; failures attributed to
external factors (market conditions, macro environment, supply chain, FX headwinds).
Score LOW when: attribution is balanced — management acknowledges both internal drivers
of success and internal causes of shortfalls.
  0-6   = Balanced attribution: consistent framing of causes across good and bad results
  7-12  = Mild: slight tendency to credit internally for wins, externalize losses
  13-19 = Significant asymmetry: clear pattern of internal credit / external blame
  20-25 = Extreme: all positives are management achievements, all negatives are external"""


USER_TEMPLATE = """Score this 8-K Item 2.02 earnings text on the four hype dimensions.
Return exactly one JSON object: {{"qo_score": integer, "se_score": integer, "cl_score": integer, "sa_score": integer}}
All values 0-25. No markdown, no preamble, no explanation.

TEXT:
{text}"""


def truncate_to_words(text: str, max_words: int) -> str:
    words = text.split()
    if len(words) <= max_words:
        return text
    return " ".join(words[:max_words])


def word_count(text: str) -> int:
    return len(text.split())


def call_ollama(text: str, previous_error: str = "") -> dict:
    retry_note = ""
    if previous_error:
        retry_note = f"\nYour previous response was rejected: {previous_error}\nReturn ONLY the JSON object.\n\n"

    payload = {
        "model": MODEL,
        "stream": False,
        "format": {
            "type": "object",
            "properties": {
                "qo_score": {"type": "integer", "minimum": 0, "maximum": 25},
                "se_score": {"type": "integer", "minimum": 0, "maximum": 25},
                "cl_score": {"type": "integer", "minimum": 0, "maximum": 25},
                "sa_score": {"type": "integer", "minimum": 0, "maximum": 25},
            },
            "required": ["qo_score", "se_score", "cl_score", "sa_score"],
            "additionalProperties": False,
        },
        "think": False,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": "/no_think\n" + retry_note + USER_TEMPLATE.format(text=text)},
        ],
        "options": {
            "temperature": 0,
            "top_k": 1,
            "top_p": 0.1,
            "seed": 42,
            "num_predict": 64,
        },
    }

    req = urllib.request.Request(
        f"{OLLAMA_URL.rstrip('/')}/api/chat",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        data = json.loads(resp.read().decode("utf-8"))

    content = data.get("message", {}).get("content", "")
    if not content:
        raise ValueError("Ollama returned empty content")

    parsed = json.loads(content)
    for key in ("qo_score", "se_score", "cl_score", "sa_score"):
        if key not in parsed:
            raise ValueError(f"Missing key: {key}")
        v = parsed[key]
        if not isinstance(v, int) or not (0 <= v <= 25):
            raise ValueError(f"{key}={v} out of range 0-25")

    return parsed


def score_with_retries(text: str) -> dict | None:
    previous_error = ""
    for attempt in range(RETRIES):
        try:
            return call_ollama(text, previous_error)
        except Exception as exc:
            previous_error = str(exc)
            if attempt < RETRIES - 1:
                time.sleep(2 ** attempt)
    return None


def load_already_scored() -> set:
    done = set()
    if not RESUME or not os.path.exists(OUTPUT_CSV):
        return done
    with open(OUTPUT_CSV, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            key = (row["ticker"], row["cik"], row["filingDate"], row["accessionNumber"])
            done.add(key)
    return done


def main():
    print(f"[{datetime.now():%H:%M:%S}] Loading input CSV...")
    rows = []
    with open(INPUT_CSV, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            rows.append(row)
    print(f"[{datetime.now():%H:%M:%S}] Loaded {len(rows):,} rows.")

    # Filter by minimum word count
    rows = [r for r in rows if word_count(r.get("item_202_text", "")) >= MIN_WORDS]
    print(f"[{datetime.now():%H:%M:%S}] {len(rows):,} rows after minimum word filter ({MIN_WORDS} words).")

    # Sample
    if SAMPLE_SIZE > 0 and len(rows) > SAMPLE_SIZE:
        random.seed(RANDOM_SEED)
        rows = random.sample(rows, SAMPLE_SIZE)
        print(f"[{datetime.now():%H:%M:%S}] Sampled {SAMPLE_SIZE} rows (seed={RANDOM_SEED}).")

    # Resume: skip already scored
    already_done = load_already_scored()
    if already_done:
        print(f"[{datetime.now():%H:%M:%S}] Resuming — skipping {len(already_done)} already-scored rows.")
    todo = [
        r for r in rows
        if (r["ticker"], r["cik"], r["filingDate"], r["accessionNumber"]) not in already_done
    ]
    print(f"[{datetime.now():%H:%M:%S}] {len(todo)} rows to score.")

    # Output file setup
    output_exists = os.path.exists(OUTPUT_CSV)
    out_fields = ["ticker", "cik", "filingDate", "accessionNumber", "source",
                  "qo_score", "se_score", "cl_score", "sa_score", "hype_score", "word_count"]
    err_fields = ["ticker", "cik", "filingDate", "accessionNumber", "error"]

    scored = 0
    errors = 0

    with open(OUTPUT_CSV, "a", newline="", encoding="utf-8") as out_f, \
         open(ERRORS_CSV, "a", newline="", encoding="utf-8") as err_f:

        out_writer = csv.DictWriter(out_f, fieldnames=out_fields)
        err_writer = csv.DictWriter(err_f, fieldnames=err_fields)

        if not output_exists:
            out_writer.writeheader()
        if not os.path.exists(ERRORS_CSV) or os.path.getsize(ERRORS_CSV) == 0:
            err_writer.writeheader()

        for i, row in enumerate(todo, 1):
            text = row.get("item_202_text", "")
            text_truncated = truncate_to_words(text, MAX_WORDS)
            wc = word_count(text_truncated)

            result = score_with_retries(text_truncated)

            if result is None:
                errors += 1
                err_writer.writerow({
                    "ticker": row["ticker"],
                    "cik": row["cik"],
                    "filingDate": row["filingDate"],
                    "accessionNumber": row["accessionNumber"],
                    "error": "failed after retries",
                })
                err_f.flush()
            else:
                scored += 1
                hype_score = result["qo_score"] + result["se_score"] + result["cl_score"] + result["sa_score"]
                out_writer.writerow({
                    "ticker": row["ticker"],
                    "cik": row["cik"],
                    "filingDate": row["filingDate"],
                    "accessionNumber": row["accessionNumber"],
                    "source": row.get("source", ""),
                    "qo_score": result["qo_score"],
                    "se_score": result["se_score"],
                    "cl_score": result["cl_score"],
                    "sa_score": result["sa_score"],
                    "hype_score": hype_score,
                    "word_count": wc,
                })
                out_f.flush()

            if i % PROGRESS_EVERY == 0 or i == len(todo):
                pct = 100 * i / len(todo)
                print(f"[{datetime.now():%H:%M:%S}] {i}/{len(todo)} ({pct:.0f}%) | scored={scored} errors={errors}")

    print(f"\n[{datetime.now():%H:%M:%S}] DONE. Scored: {scored} | Errors: {errors}")
    print(f"Output: {OUTPUT_CSV}")


if __name__ == "__main__":
    main()
