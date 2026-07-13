# FINTech Hype Analysis

Local, resumable Python pipeline for scoring hype and vagueness in SEC Form 8-K
Item 2.02 earnings-release text. The project uses Ollama locally and otherwise
depends only on the Python standard library.

## Requirements

- Python 3.10+
- Ollama installed and running locally
- A local Ollama model, currently configured as `qwen3:8b`

Install Python dependencies:

```powershell
pip install -r requirements.txt
```

`requirements.txt` is intentionally empty except for comments because the
runtime uses only the standard library. Ollama is an external service, not a pip
dependency.

Check local Ollama models:

```powershell
ollama list
ollama ps
```

## Run

Runtime settings are defined in [src/config.py](src/config.py). For a small
smoke run, temporarily set:

```python
LIMIT = 2
OUTPUT_CSV = PROJECT_ROOT / "outputs/test_scores.csv"
ERRORS_CSV = PROJECT_ROOT / "outputs/test_errors.csv"
```

Then run:

```powershell
python src/main.py
```

The output CSV is appended as rows complete. With `RESUME = True`, rerunning the
pipeline skips rows whose metadata key is already present in the output CSV.

Optional diagnostics:

```powershell
python src/calibrate.py
python src/rescore.py
python -m unittest discover -s tests
python -m compileall -q src tests
```

`src/calibrate.py` prints a simple histogram for the configured output score
file.

`src/rescore.py` adds another final-score column to the completed pipeline CSV.
It uses only the stored census fields and does not call Ollama:

```powershell
python src/rescore.py
```

Pass the column name and a callable that accepts the count census:

```python
add_score_column(
    csv_path,
    "score_v2",
    lambda census: 100 * census["sentences_vague"] / census["sentences_total"],
)
```

CSV files are row-oriented, so adding a column requires rewriting the file.
The script writes a temporary file and atomically replaces the original.

## Configuration

Important defaults in [src/config.py](src/config.py):

```python
INPUT_CSV = PROJECT_ROOT / "resources/item202_clean.csv"
OUTPUT_CSV = PROJECT_ROOT / "outputs/item202_hype_vagueness_scores.csv"
ERRORS_CSV = PROJECT_ROOT / "outputs/item202_hype_vagueness_errors.csv"
PROMPT_FILE = PROJECT_ROOT / "prompts/hype_vagueness_score.md"
VALIDATION_PROMPT_FILE = PROJECT_ROOT / "prompts/hype_vagueness_validation.md"
LOG_FILE = PROJECT_ROOT / "outputs/scoring.log"

MODEL = "qwen3:8b"
OLLAMA_URL = "http://localhost:11434"

CONCURRENCY = 1
QUEUE_MULTIPLIER = 4
REQUEST_TIMEOUT_SECONDS = 300
RETRIES = 4
VALIDATION_PCT = 0.1
LIMIT = 0
RESUME = True
PROGRESS_EVERY = 25
TERMINAL_STATUS_EVERY = 1
DASHBOARD_ENABLED = True
DASHBOARD_HOST = "127.0.0.1"
DASHBOARD_PORT = 8765
```

`CONCURRENCY = 1` is the conservative default for long-context local Ollama
runs. Increase it only after checking GPU memory, CPU offload, and timeout
behavior on the target machine.

## Data Contract

The default input CSV is ignored by git and expected at:

```text
resources/item202_clean.csv
```

Required input columns:

```text
ticker,cik,filingDate,accessionNumber,source,item_202_text
```

Resume identity is built from:

```text
ticker,cik,filingDate,accessionNumber,source
```

Those metadata fields are joined in [src/rows.py](src/rows.py) to build the
stable resume key.

## Output Schemas

Score output CSV:

```text
ticker,cik,filingDate,accessionNumber,source,score,reasoning,validation_status,validation_reasoning,original_score,original_reasoning,sentences_total,sentences_concrete,sentences_vague,promotional_terms,buzzword_hedge_terms,distinct_figures
```

Validation statuses:

- `not_selected`: row was not selected for validation.
- `accepted`: validation accepted the count census.
- `corrected`: validation returned corrected count values.

Error output CSV:

```text
ticker,cik,filingDate,accessionNumber,source,error_stage,error_type,error_message,prompt_target,model_output_preview,error
```

Generated outputs, raw resources, editor files, caches, and `.env` are ignored
by git.

## Scoring Method

The model does not return the final score directly. It returns a six-field count
census:

```json
{
  "sentences_total": 0,
  "sentences_concrete": 0,
  "sentences_vague": 0,
  "promotional_terms": 0,
  "buzzword_hedge_terms": 0,
  "distinct_figures": 0
}
```

[src/scoring.py](src/scoring.py) converts those counts into a deterministic
0-100 score. Higher scores indicate more vague, promotional, or weakly supported
language. Lower scores indicate more concrete, quantified, neutral disclosure.
This count-then-normalize design follows common financial-text analysis practice:
classify auditable textual features first, normalize by document length, and keep
the downstream score deterministic rather than asking the language model for a
subjective scalar judgment.

Rows selected by `VALIDATION_PCT` receive a second Ollama call using
[prompts/hype_vagueness_validation.md](prompts/hype_vagueness_validation.md).
Validation either accepts the census or returns corrected counts. Python then
recomputes the final score from the accepted or corrected counts.

Methodological references:

- Loughran, T., & McDonald, B. (2011). When is a liability not a liability?
  Textual analysis, dictionaries, and 10-Ks. *The Journal of Finance*, 66(1),
  35-65.
- Loughran, T., & McDonald, B. (2016). Textual analysis in accounting and
  finance: A survey. *Journal of Accounting Research*, 54(4), 1187-1230.
- Henry, E. (2008). Are investors influenced by how earnings press releases are
  written? *The Journal of Business Communication*, 45(4), 363-407.

## Monitoring

When `DASHBOARD_ENABLED = True`, the local dashboard is available while the
pipeline runs:

```text
http://127.0.0.1:8765
```

Dashboard JSON endpoint:

```text
http://127.0.0.1:8765/status
```

The dashboard and log file track processed rows, completed rows, failures,
skipped rows, pending work, validation counts, correction counts, throughput,
elapsed time, and ETA. The log file is written to `outputs/scoring.log`.

## Repository Layout

```text
.
|-- README.md
|-- requirements.txt
|-- prompts/
|   |-- hype_vagueness_score.md
|   `-- hype_vagueness_validation.md
|-- src/
|   |-- __init__.py
|   |-- calibrate.py
|   |-- config.py
|   |-- dashboard/
|   |   |-- dashboard.css
|   |   |-- dashboard.js
|   |   `-- index.html
|   |-- main.py
|   |-- monitoring.py
|   |-- ollama_scorer.py
|   |-- pipeline.py
|   |-- repeatability.py
|   |-- rows.py
|   `-- scoring.py
`-- tests/
    |-- test_main_repeatability.py
    |-- test_main_run_mode.py
    |-- test_monitoring.py
    |-- test_scoring_output.py
    `-- test_vagueness_score.py
```

`resources/` and `outputs/` are intentionally omitted from git by `.gitignore`.

## Development Checks

Run before pushing:

```powershell
python -m unittest discover -s tests
python -m compileall -q src tests
git diff --check
```
