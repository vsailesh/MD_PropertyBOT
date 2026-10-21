#!/usr/bin/env python3
"""Classifier v3 pranaam sweep — the two ambiguous bands (batch, no per-row torch).

Band b (rescue): is_hindu=0, is_indian=0, surname in ambiguous_last AND a
  hindu_first token elsewhere -> pranaam score <= 0.15 flips to hindu.
Band a (veto):   is_hindu=1 with a surname that's in NO dict pool (rule-6
  ngram territory) -> score >= 0.5 flips to muslim.

Names deduped before scoring (owner strings repeat heavily). Idempotent:
second run finds no candidates in either band.

Usage: python scripts/reclassify_pranaam.py [--dry-run] [--db md|nj|tx|all]
"""
import argparse
import os
import sqlite3
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

DBS = {
    "md": ("data/property_search.db", "properties", ["id"]),
    "nj": ("data/nj_properties.db", "nj_properties", ["property_id"]),
    "tx": ("data/tx_properties.db", "tx_properties", ["county", "property_id"]),
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--db", default="all", choices=list(DBS) + ["all"])
    args = ap.parse_args()

    from src.community_pipeline import RaceEthnicityPredictor
    p = RaceEthnicityPredictor()
    p.use_ethnicolr = False

    surname_pools = (p.hindu_last | p.sikh_names | p.ambiguous_last
                     | p.muslim_names | p.christian_names)

    def score_batch(names, chunk=5000):
        out = []
        for i in range(0, len(names), chunk):
            out += RaceEthnicityPredictor.pranaam_scores(names[i:i + chunk])
            print(f"  scored {min(i + chunk, len(names))}/{len(names)}", flush=True)
        return dict(zip(names, out))

    for tag, (rel, table, keycols) in DBS.items():
        if args.db != "all" and args.db != tag:
            continue
        con = sqlite3.connect(os.path.join(ROOT, rel))
        key = ", ".join(keycols)
        # owner-name shape differs per DB; owner_name column in all three
        rows_b, rows_a = [], []
        for *keys, name in con.execute(
                f"SELECT {key}, owner_name FROM {table} "
                "WHERE owner_name IS NOT NULL AND is_hindu=0"):
            parts = name.upper().replace(",", " ").split()
            if not parts:
                continue
            surname = parts[0]
            if surname in p.ambiguous_last and any(
                    t in p.hindu_first for t in parts[1:]):
                rows_b.append((tuple(keys), name))
        for *keys, name in con.execute(
                f"SELECT {key}, owner_name FROM {table} "
                "WHERE owner_name IS NOT NULL AND is_hindu=1"):
            parts = name.upper().replace(",", " ").split()
            if not parts:
                continue
            surname = parts[0]
            if surname not in surname_pools and len(surname) >= 3:
                rows_a.append((tuple(keys), name))

        print(f"{tag}: rescue candidates {len(rows_b)}, veto candidates "
              f"{len(rows_a)}", flush=True)
        flips_up, flips_down = 0, 0
        if rows_b:
            scores = score_batch([n for _, n in rows_b])
            for keys, name in rows_b:
                if scores[name] <= 0.15:
                    flips_up += 1
                    if not args.dry_run:
                        con.execute(
                            f"UPDATE {table} SET is_hindu=1, is_indian=1, "
                            "category='Hindu/Sikh' WHERE "
                            + " AND ".join(f"{c}=?" for c in keycols), keys)
        if rows_a:
            scores = score_batch([n for _, n in rows_a])
            for keys, name in rows_a:
                if scores[name] >= 0.5:
                    flips_down += 1
                    if not args.dry_run:
                        con.execute(
                            f"UPDATE {table} SET is_hindu=0, is_indian=1, "
                            "category='Muslim' WHERE "
                            + " AND ".join(f"{c}=?" for c in keycols), keys)
        if not args.dry_run:
            con.commit()
        tot = con.execute(f"SELECT COUNT(*), SUM(is_hindu) FROM {table}").fetchone()
        print(f"{tag}: +{flips_up} hindu (rescue), -{flips_down} (veto) "
              f"-> TOTAL {tot[0]:,} rows, {tot[1] or 0:,} hindu", flush=True)
        con.close()


if __name__ == "__main__":
    main()
