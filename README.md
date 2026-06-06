# FINTech Hype Analysis

Local, resumable pipeline for scoring the hype/vagueness of SEC 8-K Item 2.02 texts.

The project uses a free local Ollama model. The default input is:

```text
resources/item202_clean.csv
```

Each input row is one 8-K. The scorer reads `item_202_text` and writes:

```text
ticker,cik,filingDate,accessionNumber,source,score
```

## Requirements

- Python 3.10+
- Ollama installed and running
- A local Ollama model, for example `qwen3:8b`

No Python packages are required beyond the standard library. `requirements.txt` is intentionally empty except for comments.

Check local models:

```powershell
ollama list
```

If needed, pull a free model:

```powershell
ollama pull llama3.1
```

or:

```powershell
ollama pull qwen3:8b
```

## Quick Test

For a quick test, edit these values in `src/config.py`:

```python
LIMIT = 2
CONCURRENCY = 1
OUTPUT_CSV = PROJECT_ROOT / "outputs/test_scores.csv"
ERRORS_CSV = PROJECT_ROOT / "outputs/test_errors.csv"
```

Then run:

```powershell
python src/main.py
```

## Full Run

Use these default config values in `src/config.py`:

```python
INPUT_CSV = PROJECT_ROOT / "resources/item202_clean.csv"
OUTPUT_CSV = PROJECT_ROOT / "outputs/item202_hype_vagueness_scores.csv"
ERRORS_CSV = PROJECT_ROOT / "outputs/item202_hype_vagueness_errors.csv"
LOG_FILE = PROJECT_ROOT / "outputs/scoring.log"
MODEL = "qwen3:8b"
CONCURRENCY = 2
LIMIT = 0
TERMINAL_STATUS_EVERY = 1
DASHBOARD_ENABLED = True
DASHBOARD_PORT = 8765
```

Then run:

```powershell
python src/main.py
```

The output is appended as rows finish. If the process stops, run the script again; already scored rows are skipped when `RESUME = True`.

## Configuration

All runtime settings are hardcoded in `src/config.py`.

- `CONCURRENCY = 2`: number of parallel Ollama requests. Increase only if your machine and Ollama setup can handle it.
- `LIMIT = 0`: process all rows. Use a positive number for testing.
- `RESUME = True`: skip rows already present in the output CSV.
- `TERMINAL_STATUS_EVERY = 1`: show every currently scored row in the terminal. Use `25` to show every 25th row, or `0` to disable current-row terminal status.
- `DASHBOARD_ENABLED = True`: start the local web dashboard.
- `DASHBOARD_PORT = 8765`: dashboard port for `http://127.0.0.1:8765`.
- `PROMPT_FILE = PROJECT_ROOT / "prompts/hype_vagueness_score.md"`: scoring rubric used by the model.
- `LOG_FILE = PROJECT_ROOT / "outputs/scoring.log"`: persistent run log with progress and failure details.

## Monitoring

High-frequency current-row status is written only to the terminal and overwrites itself on one line.

Lower-frequency checkpoints are written to both the terminal and `outputs/scoring.log`.

If `DASHBOARD_ENABLED = True`, open this URL while the script is running:

```text
http://127.0.0.1:8765
```

The dashboard shows active worker rows, counters, throughput, ETA, and recent errors. Its JSON endpoint is:

```text
http://127.0.0.1:8765/status
```

Current-row terminal status includes:

- currently scored row number
- currently scored ticker
- currently scored filing date
- currently scored accession number
- currently scored text character count

Log-file checkpoint lines are written after result CSV buffers are flushed and include:

- processed rows
- completed rows
- failed rows
- skipped rows
- submitted rows
- pending worker queue size
- rows per minute
- elapsed time
- ETA

Rows that fail after retries are also written to `outputs/item202_hype_vagueness_errors.csv`.

## Scoring

The prompt asks for one float from `0.0` to `10.0`.

Low score:

- precise
- factual
- quantified
- accounting/GAAP-heavy
- routine boilerplate

High score:

- promotional
- vague
- buzzword-heavy
- unsupported optimism
- broad forward-looking claims without concrete detail

## Project Files

- `src/main.py`: small entrypoint.
- `src/config.py`: hardcoded runtime settings.
- `src/pipeline.py`: CSV orchestration, resume logic, concurrency, output writing.
- `src/ollama_scorer.py`: Ollama request, structured output parsing, retry feedback.
- `src/monitoring.py`: console status, logging, and local web dashboard.
- `src/rows.py`: shared row metadata helpers.
- `prompts/hype_vagueness_score.md`: scoring rubric.
- `resources/item202_clean.csv`: input data.
- `outputs/item202_hype_vagueness_scores.csv`: default output.
- `outputs/item202_hype_vagueness_errors.csv`: rows that failed after retries.
- `outputs/scoring.log`: progress and monitoring log.
