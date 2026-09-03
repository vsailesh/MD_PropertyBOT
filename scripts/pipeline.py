#!/usr/bin/env python3
"""
One-command pipeline: audit coverage → scrape gaps → backfill → export.

Replaces the 4-5 manual steps that had to run in exactly the right order:

  1. coverage_audit   — find under-covered streets vs MDP bulk ground truth
  2. scrape           — drain those streets via the watchdog (self-heals
                        through Cloudflare blocks; refresh semantics, so
                        stale rows are replaced not duplicated)
  3. bulk_backfill    — coordinates/city/zip from the MDP bulk dataset
  4. export_mapped    — dashboard Excel
  5. Turso upload     — OPT-IN (--upload): Turso rate-limited us, local DB
                        is the source of truth for now

Each step only runs if it has work to do; any step failing stops the run
with a clear report of where it stopped.

Usage:
  ./venv/bin/python scripts/pipeline.py                  # full run
  ./venv/bin/python scripts/pipeline.py --skip-scrape    # no SDAT (blocked hours)
  ./venv/bin/python scripts/pipeline.py --statewide      # also queue never-searched
  ./venv/bin/python scripts/pipeline.py --upload         # include Turso sync
"""
import argparse
import os
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY = os.path.join(ROOT, "venv", "bin", "python")
GAPS_XLSX = os.path.join(ROOT, "data", "coverage_gaps.xlsx")


def run_step(name, cmd, check=True, retries=0):
    for attempt in range(retries + 1):
        print(f"\n{'=' * 70}\n▶ {name}{f' (retry {attempt})' if attempt else ''}\n{'=' * 70}", flush=True)
        t0 = time.time()
        rc = subprocess.call(cmd, cwd=ROOT)
        dt = time.time() - t0
        status = "ok" if rc == 0 else f"FAILED rc={rc}"
        print(f"\n◀ {name}: {status} ({dt / 60:.1f}m)", flush=True)
        if rc == 0 or check is False:
            return rc
        # backfill is resumable and SQLite-lock-prone while the scraper is
        # live — give it another shot after the lock clears
        if attempt < retries:
            print("   retrying in 90s (scraper may hold the write lock)", flush=True)
            time.sleep(90)
    sys.exit(f"pipeline stopped at step '{name}'")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--skip-scrape", action="store_true",
                    help="skip SDAT scraping (audit+backfill+export+sync only)")
    ap.add_argument("--upload", action="store_true",
                    help="also sync to Turso (OFF by default — Turso rate-limited "
                         "us; local DB is the source of truth for now)")
    ap.add_argument("--statewide", action="store_true",
                    help="also queue never-searched statewide streets "
                         "(MDP bulk diff) before scraping")
    ap.add_argument("--min-parcels", type=int, default=1,
                    help="min parcels per statewide street (with --statewide)")
    args = ap.parse_args()

    # 1. Audit — always fresh gap list
    run_step("coverage audit", [
        PY, "scripts/coverage_audit.py", "--gaps", GAPS_XLSX,
    ])

    scraped = False
    if not args.skip_scrape:
        if os.path.exists(GAPS_XLSX):
            import pandas as pd
            n_gaps = len(pd.read_excel(GAPS_XLSX))
        else:
            n_gaps = 0

        if args.statewide:
            run_step("statewide diff (queue never-searched)", [
                PY, "scripts/statewide_streets.py",
                "--min-parcels", str(args.min_parcels),
                "--create-job",
            ])
            scraped = True  # watchdog below drains oldest batch first, then this

        if n_gaps > 0:
            print(f"\n{ n_gaps } gap streets queued for refresh re-scrape")
            run_step("scrape gaps (watchdog drain)", [
                "bash", "scripts/sdat_watchdog.sh",
                "-i", GAPS_XLSX, "--force", "--replace",
            ])
            scraped = True
        elif scraped:
            run_step("drain queued batches (watchdog)", ["bash", "scripts/sdat_watchdog.sh"])
        else:
            print("\nno coverage gaps, nothing to scrape")

    # 3. Backfill coordinates/city/zip from bulk
    run_step("bulk backfill", [
        PY, "scripts/bulk_backfill.py",
        "--backfill", "--relaxed", "--from-address",
    ], retries=2)

    # 4. Dashboard export
    run_step("export mapped", [PY, "scripts/export_mapped.py"])

    # 5. Remote sync — opt-in: Turso rate-limited us, local DB is the
    # source of truth for now
    if args.upload:
        run_step("Turso sync", [PY, "data/async_upload_to_turso.py"])
    else:
        print("\n⏭ Turso sync skipped (local-only; pass --upload to sync)")

    print("\n✅ pipeline complete")


if __name__ == "__main__":
    sys.exit(main())
