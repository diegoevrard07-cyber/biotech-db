"""
Full pipeline refresh (Rung 2), fail-soft.

Runs every stage in order. Ingestion stages are fail-soft: if one fails (network,
rate limit, delisting), it is logged and the pipeline continues so scoring still
runs on whatever data is present. The script exits non-zero if any stage marked
critical fails, or if any stage errored (so a scheduler can alert).

Order:
  apply_schema -> ingest_layer1 -> classify_universe -> compute/apply base rates
  -> run_layer4 (SEC) -> ingest_prices -> ingest_positioning -> ingest_insider
  -> resolve_outcomes -> build_event_returns -> run_composite -> validate -> calibrate
  -> action_sheet -> verify_signals

`load_companies.py` is intentionally NOT a stage: it seeds the curated universe once
per database, not on every refresh.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"

# (script, args, critical)
STAGES: list[tuple[str, list[str], bool]] = [
    ("apply_schema.py", [], False),  # ensure schema/indexes (e.g. uq_catalysts_ctgov) first
    ("ingest_layer1.py", [], False),
    ("classify_universe.py", [], False),
    ("compute_base_rates.py", [], False),
    ("apply_base_rates.py", [], False),
    ("run_layer4.py", [], False),
    ("ingest_prices.py", ["--lookback-days", "400"], False),
    ("ingest_positioning.py", [], False),
    ("ingest_insider.py", [], False),
    ("resolve_outcomes.py", [], False),
    ("build_event_returns.py", [], False),
    ("run_composite.py", [], True),
    ("validate_base_rates.py", [], False),
    ("calibrate.py", [], False),
    ("action_sheet.py", [], False),
    ("verify_signals.py", [], False),
]


def _run(name: str, args: list[str]) -> int:
    print(f"\n{'='*70}\n>>> {name} {' '.join(args)}\n{'='*70}", flush=True)
    proc = subprocess.run([sys.executable, str(SCRIPTS / name), *args], cwd=str(ROOT))
    return proc.returncode


def main() -> None:
    """CLI entry: run every pipeline stage in order (fail-soft), exit non-zero on failures."""
    parser = argparse.ArgumentParser(description="Refresh the full Rung 2 pipeline (fail-soft)")
    parser.add_argument("--skip", nargs="*", default=[], help="Script names to skip")
    args = parser.parse_args()

    results: dict[str, int] = {}
    hard_fail = False
    for name, sargs, critical in STAGES:
        if name in args.skip:
            print(f"--- skipping {name}")
            continue
        try:
            rc = _run(name, sargs)
        except Exception as exc:  # noqa: BLE001
            print(f"!!! {name} crashed: {exc}")
            rc = 1
        results[name] = rc
        if rc != 0:
            print(f"!!! {name} exited {rc}" + (" (CRITICAL)" if critical else " (continuing)"))
            if critical:
                hard_fail = True

    print(f"\n{'='*70}\nPipeline summary\n{'='*70}")
    for name, rc in results.items():
        print(f"  {'OK ' if rc == 0 else 'ERR'} {name} (exit {rc})")

    failed = [n for n, rc in results.items() if rc != 0]
    if hard_fail or failed:
        print(f"\nFAILED stages: {failed}")
        sys.exit(1)
    print("\nAll stages succeeded.")


if __name__ == "__main__":
    main()
