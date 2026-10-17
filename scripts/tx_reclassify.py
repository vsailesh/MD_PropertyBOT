#!/usr/bin/env python3
"""Re-run the v2 classifier over tx_properties.owner_name (no HTTP).

Run after dict changes (e.g. muslim variant additions) or after a fetch
crashed mid-county. Idempotent: second run reports 0 changed.

Usage: python scripts/tx_reclassify.py [--dry-run]
"""
import argparse
import os
import sqlite3
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

DB_PATH = os.path.join(ROOT, "data", "tx_properties.db")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    from src.community_pipeline import RaceEthnicityPredictor
    predictor = RaceEthnicityPredictor()
    predictor.use_ethnicolr = False

    con = sqlite3.connect(DB_PATH)
    rows = con.execute(
        "SELECT county, property_id, owner_name, is_hindu, category "
        "FROM tx_properties WHERE owner_name IS NOT NULL").fetchall()
    print(f"{len(rows):,} rows")

    n_flip_h, n_flip_out, n_cat = 0, 0, 0
    for county, pid, name, old_hindu, old_cat in rows:
        res = predictor.predict_race(name)
        new_hindu = 1 if res.get("is_hindu") else 0
        new_cat = res.get("sub_category") or res.get("category") or ""
        if new_hindu != old_hindu:
            if new_hindu:
                n_flip_h += 1
            else:
                n_flip_out += 1
            n_cat += (new_cat != (old_cat or ""))
        if not args.dry_run and (new_hindu != old_hindu or new_cat != (old_cat or "")):
            con.execute(
                "UPDATE tx_properties SET is_hindu=?, is_indian=?, category=? "
                "WHERE county=? AND property_id=?",
                (new_hindu, 1 if res.get("is_indian") else 0, new_cat,
                 county, pid))
    if not args.dry_run:
        con.commit()
    tot = con.execute("SELECT COUNT(*), SUM(is_hindu) FROM tx_properties").fetchone()
    print(f"hindu gained {n_flip_h}, lost {n_flip_out}, cat changed {n_cat}")
    print(f"TOTAL {tot[0]:,} rows, {tot[1] or 0:,} hindu")
    con.close()


if __name__ == "__main__":
    main()
