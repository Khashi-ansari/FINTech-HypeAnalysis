from __future__ import annotations

import csv
import time
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from pathlib import Path

try:
    from . import config as cfg
    from .monitoring import (
        LOGGER,
        add_monitor_error,
        clear_terminal_status,
        format_duration,
        init_monitor_state,
        log_progress,
        log_results_written_if_needed,
        log_run_start,
        mark_finished,
        setup_logging,
        start_dashboard,
        update_monitor_progress,
    )
    from .ollama_scorer import score_with_retries
    from .rows import ERROR_FIELDNAMES, OUTPUT_FIELDNAMES, metadata, row_key
except ImportError:  # Allows running via python src/main.py
    import config as cfg
    from monitoring import (
        LOGGER,
        add_monitor_error,
        clear_terminal_status,
        format_duration,
        init_monitor_state,
        log_progress,
        log_results_written_if_needed,
        log_run_start,
        mark_finished,
        setup_logging,
        start_dashboard,
        update_monitor_progress,
    )
    from ollama_scorer import score_with_retries
    from rows import ERROR_FIELDNAMES, OUTPUT_FIELDNAMES, metadata, row_key


def load_prompt() -> str:
    """Read the hype/vagueness scoring prompt from disk."""
    return cfg.PROMPT_FILE.read_text(encoding="utf-8")


def count_input_rows() -> int:
    """Count input rows once so progress logs can include percent and ETA."""
    with cfg.INPUT_CSV.open("r", encoding="utf-8-sig", newline="") as file:
        return sum(1 for _ in csv.DictReader(file))


def read_completed_keys() -> set[str]:
    """Read already scored filing keys from the output CSV for resume support."""
    if not cfg.OUTPUT_CSV.exists() or cfg.OUTPUT_CSV.stat().st_size == 0:
        return set()

    completed: set[str] = set()
    with cfg.OUTPUT_CSV.open("r", encoding="utf-8-sig", newline="") as file:
        reader = csv.DictReader(file)
        for row in reader:
            if row["score"].strip():
                completed.add(row_key(row))
    return completed


def ensure_csv_file(path: Path, fieldnames: list[str]) -> None:
    """Create a CSV file or migrate its header to include newly required columns."""
    path.parent.mkdir(parents=True, exist_ok=True)

    if not path.exists() or path.stat().st_size == 0:
        with path.open("w", encoding="utf-8", newline="") as file:
            csv.DictWriter(file, fieldnames=fieldnames).writeheader()
        return

    with path.open("r", encoding="utf-8-sig", newline="") as file:
        reader = csv.DictReader(file)
        current_fieldnames = reader.fieldnames or []
        if all(field in current_fieldnames for field in fieldnames):
            return
        rows = list(reader)

    with path.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in fieldnames})


def ensure_output_files() -> None:
    """Create output and error CSV files with the expected headers if needed."""
    ensure_csv_file(cfg.OUTPUT_CSV, OUTPUT_FIELDNAMES)
    ensure_csv_file(cfg.ERRORS_CSV, ERROR_FIELDNAMES)


def write_finished(
    futures: set[Future[dict[str, str]]],
    output_writer: csv.DictWriter[str],
    error_writer: csv.DictWriter[str],
    progress: dict[str, int],
    target_rows: int,
    start_time: float,
    pending_count: int,
    completed_before_run: int,
) -> None:
    """Write completed futures to the score CSV or error CSV and update progress."""
    for future in futures:
        row = getattr(future, "input_row")
        try:
            result = future.result()
            output_writer.writerow(result)
            progress["completed"] += 1
        except Exception as exc:  # Keep long runs moving; failed rows can be rerun later.
            error_writer.writerow({**metadata(row), "error": str(exc)})
            progress["failed"] += 1
            add_monitor_error(row, exc)
            clear_terminal_status()
            LOGGER.error(
                "row failed ticker=%s cik=%s filingDate=%s accession=%s source=%s error=%s",
                row["ticker"],
                row["cik"],
                row["filingDate"],
                row["accessionNumber"],
                row["source"],
                exc,
            )

        done = progress["completed"] + progress["failed"]
        if cfg.PROGRESS_EVERY > 0 and done - progress["last_logged"] >= cfg.PROGRESS_EVERY:
            progress["log_after_flush"] = 1

        update_monitor_progress(progress, target_rows, start_time, pending_count, completed_before_run)


def run_score() -> None:
    """Run the full parallel scoring pipeline over the configured input CSV."""
    setup_logging()
    start_time = time.monotonic()
    prompt = load_prompt()
    completed_keys = read_completed_keys() if cfg.RESUME else set()
    completed_before_run = len(completed_keys)
    total_rows = count_input_rows()
    available_rows = max(total_rows - completed_before_run, 0) if cfg.RESUME else total_rows
    target_rows = min(cfg.LIMIT, available_rows) if cfg.LIMIT else available_rows

    log_run_start(total_rows, target_rows, completed_before_run)
    init_monitor_state(total_rows, target_rows, completed_before_run, start_time)
    start_dashboard()
    if completed_keys:
        LOGGER.info("resume enabled: skipping %s already scored rows", completed_before_run)

    ensure_output_files()

    progress = {"submitted": 0, "completed": 0, "failed": 0, "skipped": 0, "last_logged": 0, "log_after_flush": 0}
    queue_size = cfg.CONCURRENCY * cfg.QUEUE_MULTIPLIER
    pending: set[Future[dict[str, str]]] = set()

    with (
        cfg.INPUT_CSV.open("r", encoding="utf-8-sig", newline="") as input_file,
        cfg.OUTPUT_CSV.open("a", encoding="utf-8", newline="") as output_file,
        cfg.ERRORS_CSV.open("a", encoding="utf-8", newline="") as errors_file,
        ThreadPoolExecutor(max_workers=cfg.CONCURRENCY) as executor,
    ):
        reader = csv.DictReader(input_file)
        output_writer = csv.DictWriter(
            output_file,
            fieldnames=OUTPUT_FIELDNAMES,
        )
        error_writer = csv.DictWriter(
            errors_file,
            fieldnames=ERROR_FIELDNAMES,
        )

        for row_number, row in enumerate(reader, start=1):
            if cfg.LIMIT and progress["submitted"] >= cfg.LIMIT:
                break

            if cfg.RESUME and row_key(row) in completed_keys:
                progress["skipped"] += 1
                update_monitor_progress(progress, target_rows, start_time, len(pending), completed_before_run)
                continue

            future = executor.submit(score_with_retries, row, prompt, row_number)
            setattr(future, "input_row", row)
            pending.add(future)
            progress["submitted"] += 1
            update_monitor_progress(progress, target_rows, start_time, len(pending), completed_before_run)

            if len(pending) >= queue_size:
                finished, pending = wait(pending, return_when=FIRST_COMPLETED)
                write_finished(
                    finished,
                    output_writer,
                    error_writer,
                    progress,
                    target_rows,
                    start_time,
                    len(pending),
                    completed_before_run,
                )
                output_file.flush()
                errors_file.flush()
                if progress["log_after_flush"]:
                    progress["log_after_flush"] = 0
                    log_results_written_if_needed(
                        progress,
                        target_rows,
                        start_time,
                        len(pending),
                        completed_before_run,
                    )

        while pending:
            finished, pending = wait(pending, return_when=FIRST_COMPLETED)
            write_finished(
                finished,
                output_writer,
                error_writer,
                progress,
                target_rows,
                start_time,
                len(pending),
                completed_before_run,
            )
            output_file.flush()
            errors_file.flush()
            if progress["log_after_flush"]:
                progress["log_after_flush"] = 0
                log_results_written_if_needed(
                    progress,
                    target_rows,
                    start_time,
                    len(pending),
                    completed_before_run,
                )

    final_done = progress["completed"] + progress["failed"]
    if final_done != progress["last_logged"]:
        progress["last_logged"] = final_done
        log_progress(progress, target_rows, start_time, 0, completed_before_run)

    mark_finished()
    LOGGER.info(
        "finished submitted=%s completed=%s failed=%s skipped=%s elapsed=%s output=%s errors=%s log=%s",
        progress["submitted"],
        progress["completed"],
        progress["failed"],
        progress["skipped"],
        format_duration(time.monotonic() - start_time),
        cfg.OUTPUT_CSV,
        cfg.ERRORS_CSV,
        cfg.LOG_FILE,
    )
