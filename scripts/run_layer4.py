"""Layer 4 orchestrator: CIKs → 8-K ingest → financials → reconciliation → composite."""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = Path(__file__).resolve().parent


def _run(script: str, *extra: str, dry_run: bool = False) -> None:
    cmd = [sys.executable, str(SCRIPTS / script)]
    if dry_run and script not in ("reconcile_pdufa_dates.py",):
        cmd.append("--dry-run")
    cmd.extend(extra)
    print(f"\n>>> {' '.join(cmd)}")
    subprocess.run(cmd, cwd=str(ROOT), check=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run full Layer 4 pipeline")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--since", help="YYYY-MM-DD for 8-K window")
    parser.add_argument("--limit", type=int, help="Limit companies (smoke test)")
    parser.add_argument("--skip-fetch", action="store_true")
    parser.add_argument("--skip-financials", action="store_true")
    parser.add_argument("--skip-reconcile", action="store_true")
    parser.add_argument("--skip-composite", action="store_true")
    args = parser.parse_args()

    if not args.dry_run:
        _run("migrate_layer4_schema.py")

    _run("resolve_ciks.py", dry_run=args.dry_run)

    since_args = ["--since", args.since] if args.since else []
    limit_args = ["--limit", str(args.limit)] if args.limit else []

    if not args.skip_fetch:
        _run("fetch_filings.py", *since_args, *limit_args, dry_run=args.dry_run)

    if not args.skip_financials:
        _run("ingest_financials.py", *limit_args, dry_run=args.dry_run)

    if not args.skip_reconcile:
        rec_args = list(limit_args) + ["--create-missing"]
        if args.dry_run:
            rec_args.append("--dry-run")
        else:
            rec_args.append("--write")
        if args.since:
            rec_args.extend(["--since", args.since])
        _run("reconcile_pdufa_dates.py", *rec_args, dry_run=False)

    if not args.skip_composite and not args.dry_run:
        _run("run_composite.py", *limit_args, dry_run=False)

    if not args.dry_run:
        _run("verify_layer4.py", dry_run=False)

    print("\nLayer 4 pipeline complete.")


if __name__ == "__main__":
    main()
