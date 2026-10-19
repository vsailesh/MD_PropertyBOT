#!/usr/bin/env python3
"""Merge the swami93/indian-names-1.5M token list into the hindu dict.

The HF file is a flat 114k "name: <token>" list — given names, surnames,
muslim names, all mixed, no position info. Blindly adding it to
hindu_last_names would light up muslim and junk tokens. Safe funnel:

  1. candidate = dataset token that ACTUALLY occurs at surname position
     in our data (MD properties + TX tx_properties) — real owners only
  2. drop tokens already known (hindu/sikh/muslim/christian/ambiguous)
  3. drop business/junk tokens (business_markers)
  4. corroboration: fraction of the token's occurrences whose owner
     string ALSO carries a known-surname hit or distinctive given.
       >= 0.6  -> unambiguous hindu surname (goes to hindu_last_names)
     0.25-0.6 -> ambiguous (needs distinctive-given corroborator)
      < 0.25  -> drop (western/muslim homograph noise)

Writes a `dataset_surnames` + `dataset_ambiguous` section into
data/hindu_names_dict.json; classifier reads them via the combined
surname pool (v2 rules unchanged).

Usage: python scripts/merge_names_dataset.py [--dataset /tmp/indian_names.txt] [--dry-run]
"""
import argparse
import json
import os
import re
import sqlite3
import sys
from collections import defaultdict

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DICT_PATH = os.path.join(ROOT, "data", "hindu_names_dict.json")
MD_DB = os.path.join(ROOT, "data", "property_search.db")
TX_DB = os.path.join(ROOT, "data", "tx_properties.db")

CORROB_MIN = 0.6
AMBIG_MIN = 0.25
MIN_OCCURRENCES = 3

# tokens that pass the corroboration filter but aren't surnames we want:
# western givens, ethnonyms, trade names (manual veto over the stats)
DROP_TOKENS = {"JENNIE", "PARSI", "MAHARAJA"}


def segments(name):
    segs = []
    for seg in re.split(r"\b(?:&|AND)\b", (name or "").upper()):
        toks = [t for t in re.sub(r"[^A-Z\s]", " ", seg).split()
                if len(t) > 2 and t.isalpha()]
        if toks:
            segs.append(toks)
    return segs


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dataset", default="/tmp/indian_names.txt")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    tokens = set()
    with open(args.dataset) as f:
        for line in f:
            m = re.match(r"name:\s*(\w+)", line.strip().lower())
            if m and len(m.group(1)) > 2:
                tokens.add(m.group(1).upper())
    print(f"dataset tokens: {len(tokens):,}")

    d = json.load(open(DICT_PATH))
    known = (set(d["hindu_last_names"]) | set(d["sikh_names"])
             | set(d["muslim_names"]) | set(d.get("christian_names", []))
             | set(d.get("ambiguous_last_names", []))
             | set(d.get("hindu_first_names", []))  # already in surname pool via v2
             | set(d.get("dataset_surnames", []))
             | set(d.get("dataset_ambiguous", [])))
    business = set(d.get("business_markers", []))
    surname_pool = set(d["hindu_last_names"]) | set(d["sikh_names"])
    distinctive = set(d.get("distinctive_first_names", []))

    # scan both DBs: token at surname position -> corroborated?
    stats = defaultdict(lambda: [0, 0])  # token -> [corroborated, total]
    def scan(con, sql):
        for (name,) in con.execute(sql):
            segs = segments(name)
            if not segs:
                continue
            corr = any(
                s[0] in surname_pool
                or any(t in distinctive for t in s[1:])
                for s in segs)
            for s in segs:
                tok = s[0]
                stats[tok][0 if corr else 1] += 1

    con = sqlite3.connect(f"file:{MD_DB}?mode=ro", uri=True)
    scan(con, "SELECT owner_name FROM properties WHERE owner_name IS NOT NULL")
    con.close()
    con = sqlite3.connect(f"file:{TX_DB}?mode=ro", uri=True)
    scan(con, "SELECT owner_name FROM tx_properties WHERE owner_name IS NOT NULL")
    con.close()

    new_clear, new_ambig = [], []
    for tok, (corr, total) in sorted(stats.items()):
        if total < MIN_OCCURRENCES:
            continue
        if tok not in tokens or tok in known or tok in business or tok in DROP_TOKENS:
            continue
        rate = corr / total
        if rate >= CORROB_MIN:
            new_clear.append(tok)
        elif rate >= AMBIG_MIN:
            new_ambig.append(tok)

    print(f"candidates: {len(new_clear)} clear, {len(new_ambig)} ambiguous")
    print("clear:", new_clear)
    print("ambiguous:", new_ambig)
    if args.dry_run:
        return

    d["dataset_surnames"] = new_clear
    d["dataset_ambiguous"] = new_ambig
    # classifier pool: hindu_last_names += clear; ambiguous_last_names += ambig
    d["hindu_last_names"] = sorted(set(d["hindu_last_names"]) | set(new_clear))
    d["ambiguous_last_names"] = sorted(
        set(d.get("ambiguous_last_names", [])) | set(new_ambig))
    with open(DICT_PATH, "w") as f:
        json.dump(d, f, indent=1)
    print(f"wrote {DICT_PATH}: surnames {len(d['hindu_last_names'])}, "
          f"ambiguous {len(d['ambiguous_last_names'])}")


if __name__ == "__main__":
    main()
