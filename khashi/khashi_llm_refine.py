"""
khashi_llm_refine.py
─────────────────────────────────────────────────────────────────────────────
Scoped LLM judgment pass on AMBIGUOUS SE (Selective Emphasis) rows only.

WHY THIS FILE EXISTS
─────────────────────
khashi_scorer.py's SE component was originally designed as two stages:
  1. Python D1-D6 signal extraction (deterministic)
  2. An LLM (qwen3:8b via local Ollama) synthesising those signals into the
     final SE score + reasoning.
Stage 2 was disabled for the full 14,351-row run purely for speed (60-80s/row
locally is 5+ days at that scale). That means the finished pipeline is 100%
deterministic — zero actual agent/LLM judgment anywhere, despite being
designed for it.

This script re-adds that judgment call, but scoped and cheap:
  - Only the ~1,067 rows where the D1-D6 signals genuinely CONFLICT (strong
    promotion signals but weak concealment signals, or vice versa) get an
    LLM call. That's the ~7% of filings where the deterministic formula
    can't tell a clean story — exactly where a judgment call adds real value,
    instead of decorating a formula that already works on the other 93%.
  - Uses Groq (free tier, OpenAI-compatible API, fast inference on small
    open-weight models) instead of local Ollama, so those ~1,067 calls
    finish in minutes, not days.

THIS FILE IS FULLY INDEPENDENT of khashi_scorer.py. It does not import or
modify it. It reads the finished output CSV from the full run and produces
a SEPARATE output CSV with the LLM-refined SE score sitting alongside the
original deterministic one for direct comparison — nothing is overwritten.

HOW TO RUN
──────────
1. Get a free API key at https://console.groq.com (API Keys section).
2. Set it as an environment variable, don't hardcode it anywhere:
     Windows (PowerShell):  $env:GROQ_API_KEY = "gsk_..."
     Windows (cmd):         set GROQ_API_KEY=gsk_...
   Or create a local ".env" file next to this script (already gitignored):
     GROQ_API_KEY=gsk_...
3. pip install python-dotenv --break-system-packages   (optional, only if
   you want the .env file to be picked up automatically; otherwise just set
   the env var directly and skip this)
4. python khashi_llm_refine.py --dry-run     ← sanity-check prompts, no API calls
   python khashi_llm_refine.py               ← real run
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

# ─── CONFIGURATION ────────────────────────────────────────────────────────
# All paths are resolved relative to this script's own folder, not the
# terminal's current directory, so it works no matter where it's launched from.
SCRIPT_DIR   = os.path.dirname(os.path.abspath(__file__))
INPUT_CSV    = os.path.join(SCRIPT_DIR, "khashi_hype_full_14351.csv")   # output of the full khashi_scorer.py run
OUTPUT_CSV   = os.path.join(SCRIPT_DIR, "khashi_hype_llm_refined.csv")  # NEW file — never touches the original
ERRORS_CSV   = os.path.join(SCRIPT_DIR, "khashi_hype_llm_refined_errors.csv")
ENV_FILE     = os.path.join(SCRIPT_DIR, ".env")

GROQ_URL     = "https://api.groq.com/openai/v1/chat/completions"
GROQ_MODEL   = "llama-3.1-8b-instant"
# ^ Fast, free-tier Groq model as of when this was written. If this 404s,
#   check current model names at https://console.groq.com/docs/models
#   and swap the string above — nothing else needs to change.

CONCURRENCY  = 3      # dropped from 8 — free tier RPM couldn't sustain 8-way concurrency
MAX_RETRIES  = 6
BASE_BACKOFF = 3.0    # seconds; doubles each retry on 429 / 5xx
TIMEOUT      = 30
RESUME       = True   # skip rows already present in OUTPUT_CSV if re-run

# ─── Ambiguity rule ──────────────────────────────────────────────────────
# A row qualifies for LLM review only if the promotion signals (D1 position +
# D2 density) and the concealment signals (D4 burial + D5 demotion) actively
# disagree with each other. This is a genuine conflict in what the SE score
# should represent, not just "score near the middle" (that band is 86% of
# all rows and isn't a useful filter — checked against the real data).

def is_ambiguous(row: dict) -> bool:
    try:
        promo   = float(row["diag_se_d1"]) + float(row["diag_se_d2"])
        conceal = float(row["diag_se_d4"]) + float(row["diag_se_d5"])
    except (KeyError, ValueError):
        return False
    return (promo >= 4 and conceal <= 1) or (promo <= 1 and conceal >= 4)


# ─── Prompt (same framing as the original Stage-2 SE prompt in
#     khashi_scorer.py's call_se_signals_llm / SE_SIGNALS_SYSTEM — adapted to
#     the fields actually present in the exported diag_se_* columns, since
#     the raw filing text isn't part of that CSV, only its extracted
#     signals are, which is all the original prompt used anyway) ──────────

SE_SIGNALS_SYSTEM = """You are a senior financial disclosure analyst specialising in earnings press release framing.

You will receive structured signals automatically extracted from an S&P 500 8-K earnings filing. \
This specific filing was flagged because its signals CONFLICT: either strong promotional framing \
alongside weak concealment signals, or the reverse. Your job is to resolve that conflict and \
synthesise the signals into one SE (Selective Emphasis) score 0-25 with a short justification.

SE measures how asymmetrically a filing presents positive vs negative information, and non-GAAP vs GAAP metrics.
Genre baseline: 9-13 = typical S&P 500 8-K filing.

Score guide:
SE 0-4:   GAAP leads or co-equal. Negatives front-loaded. Symmetric framing.
SE 5-8:   Mild non-GAAP preference. Minor positive tilt.
SE 9-13:  Non-GAAP headlines, GAAP secondary, some burial. [GENRE AVERAGE]
SE 14-17: Non-GAAP dominates, negatives clearly buried or minimised.
SE 18-21: GAAP absent from prose. Significant selective omission.
SE 22-25: Extreme asymmetry — only positives shown, no GAAP, negatives absent.

Respond with ONLY a JSON object in this exact shape, nothing else:
{"se_score": <integer 0-25>, "se_reasoning": "<2 sentences, plain text>"}"""


def build_signal_block(row: dict) -> str:
    return (
        f"EXTRACTED SIGNALS from S&P 500 earnings press release (8-K):\n"
        f"- Non-GAAP share of all metric language: {float(row['diag_se_ng_ratio']):.0%}\n"
        f"- Non-GAAP language in opening headline (first 500 chars): "
        f"{row['diag_se_hl_total']} weighted hits\n"
        f"- {row['diag_se_burial_pct']}% of negative-result terms appear in the back 40% of the filing\n"
        f"- GAAP relegated-to-tables phrases: {row['diag_se_dem_hits']} instances\n"
        f"- Positive claims stated as fact: {row['diag_se_pos_fact']} | "
        f"External blame phrases: {row['diag_se_ext_blame']}\n"
        f"- Python sub-scores "
        f"[D1-Position / D2-Density / D3-Headline / D4-Burial / D5-Demotion / D6-Asymmetry]: "
        f"{row['diag_se_d1']}/{row['diag_se_d2']}/{row['diag_se_d3']}/"
        f"{row['diag_se_d4']}/{row['diag_se_d5']}/{row['diag_se_d6']} (each out of 5)\n"
        f"- Python preliminary SE estimate: {row['se_score']}/25\n\n"
        f"The D1/D2 promotion signals and D4/D5 concealment signals disagree for this filing. "
        f"Resolve the conflict and provide your SE score (0-25) and a 2-sentence reasoning."
    )


# ─── Groq call ────────────────────────────────────────────────────────────

def call_groq(api_key: str, signal_block: str) -> dict:
    payload = {
        "model": GROQ_MODEL,
        "temperature": 0,
        "max_tokens": 200,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": SE_SIGNALS_SYSTEM},
            {"role": "user", "content": signal_block},
        ],
    }
    req = urllib.request.Request(
        GROQ_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
            # Cloudflare (in front of Groq's API) blocks the default
            # Python-urllib User-Agent as a bot signature (error code 1010).
            # A normal browser-looking UA clears it.
            "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                            "AppleWebKit/537.36 (KHTML, like Gecko) "
                            "Chrome/124.0.0.0 Safari/537.36"),
            "Accept": "application/json",
        },
        method="POST",
    )

    last_err = None
    for attempt in range(MAX_RETRIES):
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            content = data["choices"][0]["message"]["content"]
            parsed = json.loads(content)
            v = parsed.get("se_score")
            if not isinstance(v, int) or not (0 <= v <= 25):
                raise ValueError(f"se_score={v!r} out of range 0-25")
            if not isinstance(parsed.get("se_reasoning"), str):
                raise ValueError("se_reasoning missing or not a string")
            return parsed
        except urllib.error.HTTPError as e:
            try:
                body = e.read().decode("utf-8", errors="replace")
            except Exception:
                body = "(no body)"
            last_err = RuntimeError(f"HTTP {e.code}: {body}")
            if e.code == 429 or e.code >= 500:
                time.sleep(BASE_BACKOFF * (2 ** attempt))
                continue
            raise last_err
        except Exception as e:
            last_err = e
            time.sleep(BASE_BACKOFF * (2 ** attempt))
            continue
    raise RuntimeError(f"Failed after {MAX_RETRIES} attempts: {last_err}")


# ─── Row worker ───────────────────────────────────────────────────────────

def process_row(api_key: str, row: dict) -> dict:
    signal_block = build_signal_block(row)
    result = call_groq(api_key, signal_block)
    return {
        "ticker":          row["ticker"],
        "cik":             row["cik"],
        "filingDate":      row["filingDate"],
        "accessionNumber": row["accessionNumber"],
        "se_score":        row["se_score"],
        "se_score_llm":    result["se_score"],
        "se_reasoning_llm": result["se_reasoning"],
        "abs_diff":        abs(int(row["se_score"]) - int(result["se_score"])),
        "hype_v3":         row.get("hype_v3", ""),
    }


# ─── Main ─────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true",
                         help="Build prompts and show the ambiguous-row count, no API calls.")
    parser.add_argument("--limit", type=int, default=0,
                         help="Only process the first N ambiguous rows (0 = all).")
    args = parser.parse_args()

    api_key = os.environ.get("GROQ_API_KEY", "")
    try:
        from dotenv import load_dotenv  # optional
        load_dotenv(ENV_FILE)
        api_key = os.environ.get("GROQ_API_KEY", api_key)
    except ImportError:
        pass

    if not args.dry_run and not api_key:
        print("GROQ_API_KEY not set. Set it as an env var or in a local .env file "
              "(see the docstring at the top of this file). Exiting.")
        sys.exit(1)

    print(f"[{datetime.now():%H:%M:%S}] Loading {INPUT_CSV} ...")
    with open(INPUT_CSV, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
    print(f"[{datetime.now():%H:%M:%S}] {len(rows):,} total rows loaded.")

    ambiguous = [r for r in rows if is_ambiguous(r)]
    print(f"[{datetime.now():%H:%M:%S}] {len(ambiguous):,} ambiguous rows "
          f"({len(ambiguous)/len(rows):.1%} of total) qualify for LLM review.")

    if args.dry_run:
        print(f"\n[{datetime.now():%H:%M:%S}] DRY RUN — example prompt for row 1:\n")
        print(build_signal_block(ambiguous[0]))
        print(f"\n[{datetime.now():%H:%M:%S}] Dry run complete. No API calls made. "
              f"Run without --dry-run once GROQ_API_KEY is set.")
        return

    already_done = set()
    out_rows = []
    if RESUME and os.path.exists(OUTPUT_CSV):
        with open(OUTPUT_CSV, newline="", encoding="utf-8") as f:
            for r in csv.DictReader(f):
                already_done.add(r["accessionNumber"])
                out_rows.append(r)
        print(f"[{datetime.now():%H:%M:%S}] RESUME: {len(already_done):,} rows already done, skipping.")

    # --limit applies AFTER filtering out already-done rows, so "--limit 10"
    # always means "10 NEW rows", not "the first 10 of the full ambiguous list"
    # (which may already be entirely done).
    todo = [r for r in ambiguous if r["accessionNumber"] not in already_done]
    if args.limit > 0:
        todo = todo[:args.limit]
        print(f"[{datetime.now():%H:%M:%S}] --limit set, processing {len(todo):,} new rows only.")

    print(f"[{datetime.now():%H:%M:%S}] {len(todo):,} rows to process now, "
          f"concurrency={CONCURRENCY}, model={GROQ_MODEL}")

    errors = []
    done_count = 0
    t0 = time.time()

    with ThreadPoolExecutor(max_workers=CONCURRENCY) as pool:
        futures = {pool.submit(process_row, api_key, row): row for row in todo}
        for fut in as_completed(futures):
            row = futures[fut]
            try:
                result = fut.result()
                out_rows.append(result)
            except Exception as e:
                err_text = str(e)
                errors.append({"accessionNumber": row.get("accessionNumber", ""),
                                "ticker": row.get("ticker", ""),
                                "error": err_text})
                print(f"[{datetime.now():%H:%M:%S}] ERROR on {row.get('ticker','?')} "
                      f"{row.get('accessionNumber','?')}: {err_text[:200]}")
            done_count += 1
            if done_count % 5 == 0 or done_count == len(todo):
                elapsed = time.time() - t0
                print(f"[{datetime.now():%H:%M:%S}] {done_count}/{len(todo)} done "
                      f"({elapsed:.0f}s elapsed, {len(errors)} errors)")

            # periodic checkpoint so a crash doesn't lose progress
            if done_count % 20 == 0:
                _write_csv(OUTPUT_CSV, out_rows)
                if errors:
                    _write_csv(ERRORS_CSV, errors)

    _write_csv(OUTPUT_CSV, out_rows)
    if errors:
        _write_csv(ERRORS_CSV, errors)

    print(f"\n[{datetime.now():%H:%M:%S}] Done. {len(out_rows):,} rows in {OUTPUT_CSV}, "
          f"{len(errors)} errors" + (f" in {ERRORS_CSV}" if errors else "") + ".")

    if out_rows:
        diffs = [int(r["abs_diff"]) for r in out_rows if str(r.get("abs_diff", "")).isdigit()]
        if diffs:
            agree_within_3 = sum(1 for d in diffs if d <= 3) / len(diffs)
            print(f"[{datetime.now():%H:%M:%S}] Deterministic vs LLM agreement "
                  f"(within 3 pts): {agree_within_3:.1%}. Mean abs diff: "
                  f"{sum(diffs)/len(diffs):.1f}.")


def _write_csv(path: str, rows: list[dict]):
    if not rows:
        return
    fieldnames = list(rows[0].keys())
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


if __name__ == "__main__":
    main()
