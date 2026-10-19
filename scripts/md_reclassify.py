#!/usr/bin/env python3
"""Re-run the classifier over properties.owner_name (no HTTP).

Sibling of scripts/tx_reclassify.py for the MD DB — run after dict changes
or model changes (e.g. the rule-6 ngram scorer). Idempotent: second run
reports 0 changed.

Usage: python scripts/md_reclassify.py [--dry-run]
"""
import argparse
import os
import sqlite3
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

DB_PATH = os.path.join(ROOT, "data", "property_search.db")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    from src.community_pipeline import RaceEthnicityPredictor
    predictor = RaceEthnicityPredictor()
    predictor.use_ethnicolr = False

    con = sqlite3.connect(DB_PATH)
    rows = con.execute(
        "SELECT id, owner_name, is_hindu, sub_category FROM properties "
        "WHERE owner_name IS NOT NULL").fetchall()
    print(f"{len(rows):,} rows")

    n_flip_h, n_flip_out = 0, 0
    for pid, name, old_hindu, old_cat in rows:
        res = predictor.predict_race(name)
        new_hindu = 1 if res.get("is_hindu") else 0
        new_cat = res.get("sub_category") or res.get("category") or ""
        if new_hindu != old_hindu:
            if new_hindu:
                n_flip_h += 1
            else:
                n_flip_out += 1
        if not args.dry_run and (new_hindu != old_hindu
                                 or new_cat != (old_cat or "")):
            con.execute(
                "UPDATE properties SET is_hindu=?, predicted_race=?, "
                "sub_category=? WHERE id=?",
                (new_hindu,
                 "Indian" if res.get("is_indian") else res.get("predicted_race", "Unknown"),
                 new_cat, pid))
    if not args.dry_run:
        con.commit()
    tot = con.execute(
        "SELECT COUNT(*), SUM(is_hindu) FROM properties").fetchone()
    print(f"hindu gained {n_flip_h}, lost {n_flip_out}")
    print(f"TOTAL {tot[0]:,} rows, {tot[1] or 0:,} hindu")
    con.close()


if __name__ == "__main__":
    main()
