from __future__ import annotations

from pathlib import Path

# ---------------------------------------------------------------------------
# Configuration
# Edit these values to change how the pipeline runs.
# ---------------------------------------------------------------------------

PROJECT_ROOT = Path(__file__).resolve().parents[1]

INPUT_CSV = PROJECT_ROOT / "resources/item202_clean.csv"
OUTPUT_CSV = PROJECT_ROOT / "outputs/item202_hype_vagueness_scores.csv"
ERRORS_CSV = PROJECT_ROOT / "outputs/item202_hype_vagueness_errors.csv"
PROMPT_FILE = PROJECT_ROOT / "prompts/hype_vagueness_score.md"
LOG_FILE = PROJECT_ROOT / "outputs/scoring.log"

MODEL = "qwen3:8b"
# MODEL = "llama3.1:latest"
OLLAMA_URL = "http://localhost:11434"

CONCURRENCY = 2
QUEUE_MULTIPLIER = 4
REQUEST_TIMEOUT_SECONDS = 180
RETRIES = 2

# 0 means no row cap.
LIMIT = 0

RESUME = True
PROGRESS_EVERY = 25
TERMINAL_STATUS_EVERY = 1
DASHBOARD_ENABLED = True
DASHBOARD_HOST = "127.0.0.1"
DASHBOARD_PORT = 8765
