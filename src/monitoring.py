from __future__ import annotations

import json
import logging
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

try:
    from . import config as cfg
except ImportError:  # Allows running via python src/main.py
    import config as cfg

LOGGER = logging.getLogger("hype_vagueness_scoring")
TERMINAL_LOCK = threading.Lock()
TERMINAL_STATUS_LENGTH = 0
MONITOR_LOCK = threading.Lock()
MONITOR_STATE: dict[str, Any] = {
    "run_status": "not started",
    "queued_rows": {},
    "current_rows": {},
    "recent_scores": [],
    "recent_validations": [],
    "recent_errors": [],
}
DASHBOARD_ASSET_DIR = Path(__file__).resolve().parent / "dashboard"
DASHBOARD_ROUTES = {
    "/dashboard.css": ("dashboard.css", "text/css; charset=utf-8"),
    "/dashboard.js": ("dashboard.js", "application/javascript; charset=utf-8"),
}


def setup_logging() -> None:
    """Configure console and file logging for the scoring run."""
    cfg.LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    LOGGER.setLevel(logging.INFO)
    LOGGER.propagate = False

    for handler in LOGGER.handlers[:]:
        handler.close()
        LOGGER.removeHandler(handler)

    formatter = logging.Formatter(
        fmt="%(asctime)s | %(levelname)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)
    LOGGER.addHandler(console_handler)

    file_handler = logging.FileHandler(cfg.LOG_FILE, encoding="utf-8")
    file_handler.setFormatter(formatter)
    LOGGER.addHandler(file_handler)


def write_terminal_status(message: str) -> None:
    """Write a high-frequency terminal-only status line that overwrites itself."""
    global TERMINAL_STATUS_LENGTH

    with TERMINAL_LOCK:
        padding = " " * max(0, TERMINAL_STATUS_LENGTH - len(message))
        sys.stdout.write(f"\r{message}{padding}")
        sys.stdout.flush()
        TERMINAL_STATUS_LENGTH = len(message)


def clear_terminal_status() -> None:
    """Clear the terminal-only status line before writing regular log messages."""
    global TERMINAL_STATUS_LENGTH

    with TERMINAL_LOCK:
        if TERMINAL_STATUS_LENGTH:
            sys.stdout.write("\r" + (" " * TERMINAL_STATUS_LENGTH) + "\r")
            sys.stdout.flush()
            TERMINAL_STATUS_LENGTH = 0


def format_duration(seconds: float) -> str:
    """Format elapsed or remaining seconds as a compact human-readable duration."""
    seconds = max(0, int(seconds))
    hours, remainder = divmod(seconds, 3600)
    minutes, seconds = divmod(remainder, 60)

    if hours:
        return f"{hours}h {minutes}m {seconds}s"
    if minutes:
        return f"{minutes}m {seconds}s"
    return f"{seconds}s"


def progress_target_rows(target_rows: int, completed_before_run: int) -> int:
    """Return the progress denominator including rows completed before resume."""
    already_completed = max(completed_before_run, 0)
    return max(already_completed + max(target_rows, 0), already_completed)


def progress_metrics(
    progress: dict[str, int],
    target_rows: int,
    start_time: float,
    completed_before_run: int,
) -> dict[str, int | float]:
    """Calculate progress and ETA without letting resume rows inflate throughput."""
    run_done = progress["completed"] + progress["failed"]
    already_completed = max(completed_before_run, 0)
    processed_total = already_completed + run_done
    target_total = progress_target_rows(target_rows, already_completed)
    elapsed = time.monotonic() - start_time
    rows_per_minute = (run_done / elapsed) * 60 if elapsed > 0 else 0.0
    remaining = max(target_total - processed_total, 0)
    percent_done = min(processed_total, target_total) if target_total else processed_total
    percent = (percent_done / target_total) * 100 if target_total else 100.0
    eta_seconds = (remaining / rows_per_minute) * 60 if rows_per_minute > 0 else 0

    return {
        "already_completed": already_completed,
        "run_done": run_done,
        "processed_total": processed_total,
        "target_total": target_total,
        "elapsed": elapsed,
        "rows_per_minute": rows_per_minute,
        "remaining": remaining,
        "percent": percent,
        "eta_seconds": eta_seconds,
    }


def format_eta(remaining: int | float, rows_per_minute: float, eta_seconds: float) -> str:
    """Format ETA while avoiding fake estimates before new rows finish."""
    if remaining <= 0:
        return "0s"
    if rows_per_minute <= 0:
        return "-"
    return format_duration(eta_seconds)


def log_run_start(total_rows: int, target_rows: int, completed_before_run: int) -> None:
    """Log the run configuration and monitoring baseline."""
    LOGGER.info("starting hype/vagueness scoring")
    LOGGER.info(
        "model=%s ollama_url=%s concurrency=%s retries=%s validation_pct=%s terminal_status_every=%s",
        cfg.MODEL,
        cfg.OLLAMA_URL,
        cfg.CONCURRENCY,
        cfg.RETRIES,
        cfg.VALIDATION_PCT,
        cfg.TERMINAL_STATUS_EVERY,
    )
    LOGGER.info("input=%s", cfg.INPUT_CSV)
    LOGGER.info("output=%s", cfg.OUTPUT_CSV)
    LOGGER.info("errors=%s", cfg.ERRORS_CSV)
    LOGGER.info("log=%s", cfg.LOG_FILE)
    LOGGER.info(
        "rows_total=%s rows_target_this_run=%s rows_target_with_resume=%s resume=%s already_completed=%s limit=%s",
        total_rows,
        target_rows,
        progress_target_rows(target_rows, completed_before_run),
        cfg.RESUME,
        completed_before_run,
        cfg.LIMIT,
    )


def log_progress(
    progress: dict[str, int],
    target_rows: int,
    start_time: float,
    pending_count: int,
    completed_before_run: int,
) -> None:
    """Log a result-write checkpoint with throughput, percent done, and ETA."""
    clear_terminal_status()

    metrics = progress_metrics(progress, target_rows, start_time, completed_before_run)

    LOGGER.info(
        "results written processed=%s/%s %.1f%% already_completed=%s completed=%s failed=%s skipped=%s submitted=%s pending=%s "
        "validated=%s corrected=%s rate=%.2f rows/min elapsed=%s eta=%s",
        metrics["processed_total"],
        metrics["target_total"],
        metrics["percent"],
        metrics["already_completed"],
        progress["completed"],
        progress["failed"],
        progress["skipped"],
        progress["submitted"],
        pending_count,
        progress.get("validated", 0),
        progress.get("corrected", 0),
        metrics["rows_per_minute"],
        format_duration(metrics["elapsed"]),
        format_eta(metrics["remaining"], metrics["rows_per_minute"], metrics["eta_seconds"]),
    )


def log_results_written_if_needed(
    progress: dict[str, int],
    target_rows: int,
    start_time: float,
    pending_count: int,
    completed_before_run: int,
) -> None:
    """Log a result-write checkpoint after enough newly written rows."""
    done = progress["completed"] + progress["failed"]
    if cfg.PROGRESS_EVERY > 0 and done - progress["last_logged"] >= cfg.PROGRESS_EVERY:
        progress["last_logged"] = done
        log_progress(progress, target_rows, start_time, pending_count, completed_before_run)


def dashboard_html() -> str:
    """Build the local monitoring dashboard HTML page."""
    return read_dashboard_asset("index.html")


def read_dashboard_asset(filename: str) -> str:
    """Read a static dashboard resource bundled with the source tree."""
    return (DASHBOARD_ASSET_DIR / filename).read_text(encoding="utf-8")


class DashboardHandler(BaseHTTPRequestHandler):
    """Serve the local HTML dashboard and JSON status endpoint."""

    def do_GET(self) -> None:
        """Handle dashboard and status requests."""
        if self.path == "/":
            self.write_text_response(dashboard_html(), "text/html; charset=utf-8")
            return

        if self.path in DASHBOARD_ROUTES:
            filename, content_type = DASHBOARD_ROUTES[self.path]
            self.write_text_response(read_dashboard_asset(filename), content_type)
            return

        if self.path == "/status":
            body = json.dumps(monitor_snapshot()).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        self.send_response(404)
        self.end_headers()

    def write_text_response(self, text: str, content_type: str) -> None:
        """Write one UTF-8 dashboard text asset response."""
        body = text.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: Any) -> None:
        """Suppress per-request HTTP logging."""
        return


def monitor_snapshot() -> dict[str, Any]:
    """Return a thread-safe copy of the current dashboard state."""
    with MONITOR_LOCK:
        return {
            **MONITOR_STATE,
            "current_rows": dict(MONITOR_STATE.get("current_rows", {})),
            "queued_rows": dict(MONITOR_STATE.get("queued_rows", {})),
            "recent_scores": list(MONITOR_STATE.get("recent_scores", [])),
            "recent_validations": list(MONITOR_STATE.get("recent_validations", [])),
            "recent_errors": list(MONITOR_STATE.get("recent_errors", [])),
        }


def start_dashboard() -> ThreadingHTTPServer | None:
    """Start the optional local monitoring dashboard in a daemon thread."""
    if not cfg.DASHBOARD_ENABLED:
        return None

    try:
        server = ThreadingHTTPServer((cfg.DASHBOARD_HOST, cfg.DASHBOARD_PORT), DashboardHandler)
    except OSError as exc:
        LOGGER.warning("dashboard disabled: could not bind http://%s:%s error=%s", cfg.DASHBOARD_HOST, cfg.DASHBOARD_PORT, exc)
        return None

    thread = threading.Thread(target=server.serve_forever, name="monitor-dashboard", daemon=True)
    thread.start()
    LOGGER.info("dashboard=http://%s:%s", cfg.DASHBOARD_HOST, cfg.DASHBOARD_PORT)
    return server


def init_monitor_state(total_rows: int, target_rows: int, completed_before_run: int, start_time: float) -> None:
    """Initialize dashboard state at the start of a scoring run."""
    already_completed = max(completed_before_run, 0)
    monitored_target_rows = progress_target_rows(target_rows, already_completed)
    percent = (already_completed / monitored_target_rows) * 100 if monitored_target_rows else 100.0

    with MONITOR_LOCK:
        MONITOR_STATE.clear()
        MONITOR_STATE.update(
            {
                "run_status": "running",
                "model": cfg.MODEL,
                "ollama_url": cfg.OLLAMA_URL,
                "validation_pct": cfg.VALIDATION_PCT,
                "input": str(cfg.INPUT_CSV),
                "output": str(cfg.OUTPUT_CSV),
                "errors": str(cfg.ERRORS_CSV),
                "log": str(cfg.LOG_FILE),
                "total_rows": total_rows,
                "target_rows": monitored_target_rows,
                "target_rows_this_run": target_rows,
                "already_completed": already_completed,
                "submitted": 0,
                "completed": already_completed,
                "completed_this_run": 0,
                "failed": 0,
                "skipped": 0,
                "validated": 0,
                "corrected": 0,
                "pending": 0,
                "processed": already_completed,
                "percent": percent,
                "rows_per_minute": 0.0,
                "elapsed": "0s",
                "eta": "0s" if already_completed >= monitored_target_rows else "-",
                "start_time": start_time,
                "queued_rows": {},
                "current_rows": {},
                "recent_scores": [],
                "recent_validations": [],
                "recent_errors": [],
            }
        )


def update_monitor_progress(
    progress: dict[str, int],
    target_rows: int,
    start_time: float,
    pending_count: int,
    completed_before_run: int,
) -> None:
    """Update dashboard counters, throughput, and ETA."""
    metrics = progress_metrics(progress, target_rows, start_time, completed_before_run)

    with MONITOR_LOCK:
        MONITOR_STATE.update(
            {
                "target_rows": metrics["target_total"],
                "target_rows_this_run": target_rows,
                "already_completed": metrics["already_completed"],
                "submitted": progress["submitted"],
                "completed": metrics["already_completed"] + progress["completed"],
                "completed_this_run": progress["completed"],
                "failed": progress["failed"],
                "skipped": progress["skipped"],
                "validated": progress.get("validated", 0),
                "corrected": progress.get("corrected", 0),
                "pending": pending_count,
                "processed": metrics["processed_total"],
                "percent": metrics["percent"],
                "rows_per_minute": metrics["rows_per_minute"],
                "elapsed": format_duration(metrics["elapsed"]),
                "eta": format_eta(metrics["remaining"], metrics["rows_per_minute"], metrics["eta_seconds"]),
            }
        )


def update_monitor_current_row(
    active_id: str,
    row: dict[str, str],
    row_number: int,
    text_chars: int,
    attempt: int,
    stage: str,
) -> None:
    """Update the dashboard entry for a currently scoring worker row."""
    with MONITOR_LOCK:
        MONITOR_STATE.setdefault("queued_rows", {}).pop(active_id, None)
        MONITOR_STATE.setdefault("current_rows", {})[active_id] = {
            "row_number": row_number,
            "ticker": row["ticker"],
            "filingDate": row["filingDate"],
            "accessionNumber": row["accessionNumber"],
            "chars": text_chars,
            "attempt": attempt,
            "max_attempts": cfg.RETRIES + 1,
            "stage": stage,
        }


def add_monitor_queued_row(active_id: str, row: dict[str, str], row_number: int, text_chars: int) -> None:
    """Add a submitted row to the dashboard queue list until a worker starts it."""
    with MONITOR_LOCK:
        queued_rows = MONITOR_STATE.setdefault("queued_rows", {})
        queued_rows[active_id] = {
            "row_number": row_number,
            "ticker": row["ticker"],
            "filingDate": row["filingDate"],
            "accessionNumber": row["accessionNumber"],
            "chars": text_chars,
        }


def clear_monitor_current_row(active_id: str) -> None:
    """Remove a worker row from the dashboard active-row list."""
    with MONITOR_LOCK:
        MONITOR_STATE.setdefault("queued_rows", {}).pop(active_id, None)
        MONITOR_STATE.setdefault("current_rows", {}).pop(active_id, None)


def add_monitor_score(result: dict[str, str]) -> None:
    """Add a recent scored row entry to the dashboard state."""
    with MONITOR_LOCK:
        recent_scores = MONITOR_STATE.setdefault("recent_scores", [])
        recent_scores.insert(
            0,
            {
                "time": time.strftime("%H:%M:%S"),
                "ticker": result["ticker"],
                "filingDate": result["filingDate"],
                "accessionNumber": result["accessionNumber"],
                "score": result["score"],
                "validation_status": result.get("validation_status", ""),
                "reasoning": result.get("reasoning", ""),
            },
        )
        del recent_scores[10:]


def add_monitor_validation(result: dict[str, str]) -> None:
    """Add a recent validation entry to the dashboard state."""
    with MONITOR_LOCK:
        recent_validations = MONITOR_STATE.setdefault("recent_validations", [])
        recent_validations.insert(
            0,
            {
                "time": time.strftime("%H:%M:%S"),
                "ticker": result["ticker"],
                "filingDate": result["filingDate"],
                "accessionNumber": result["accessionNumber"],
                "score": result["score"],
                "validation_status": result.get("validation_status", ""),
                "validation_reasoning": result.get("validation_reasoning", ""),
                "original_score": result.get("original_score", ""),
            },
        )
        del recent_validations[10:]


def add_monitor_error(row: dict[str, str], error: Exception) -> None:
    """Add a recent error entry to the dashboard state."""
    with MONITOR_LOCK:
        recent_errors = MONITOR_STATE.setdefault("recent_errors", [])
        recent_errors.insert(
            0,
            {
                "time": time.strftime("%H:%M:%S"),
                "ticker": row["ticker"],
                "accessionNumber": row["accessionNumber"],
                "error": str(error),
            },
        )
        del recent_errors[20:]


def mark_finished() -> None:
    """Mark the dashboard run as finished."""
    with MONITOR_LOCK:
        MONITOR_STATE["run_status"] = "finished"
        MONITOR_STATE["pending"] = 0
