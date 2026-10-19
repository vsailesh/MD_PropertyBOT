#!/usr/bin/env python3
"""Train a char-ngram Indian-name segment scorer over our data + the bulk lists.

History (why segment-level, not token-level):
  v1 scored bare surname tokens (TF-IDF char 3-5 + LR). Calibration was
  broken — PATEL 0.44, MENON 0.08 — because (a) positives were dominated by
  HF given-name tokens so it learned given-vs-surname, not Indian-vs-west;
  (b) a bare 5-char token has too few ngrams to separate from western.
  Scoring the FULL owner segment ("MENON ARUN") carries the given name's
  signal too, and western segments collapse to ~0.

Training data:
  positives = owner segments of is_hindu rows (MD + TX), n>=2 types
  negatives = owner segments of UNflagged rows, n>=3 types (abundant,
  includes the Vietnamese/Chinese/western FP traps)
  weights   = 1+sqrt(count-1) so PATEL's 8k rows outweigh one-off typos

Eval: 20% of non-dict positive surname TOKENS held out of training; report
their segments' scores + a western trap panel. Real acceptance gate is the
reclass dry-run eyeball (labels in the DB are too noisy for offline truth).

Writes models/name_ngram.joblib ({"model", "threshold", "min_len"}).

Usage: python scripts/train_name_model.py
"""
import json
import math
import os
import random
import re
import sqlite3
import statistics
import sys
from collections import Counter

import joblib
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DICT_PATH = os.path.join(ROOT, "data", "hindu_names_dict.json")
DBS = ((os.path.join(ROOT, "data", "property_search.db"), "properties"),
       (os.path.join(ROOT, "data", "tx_properties.db"), "tx_properties"))
MODEL_PATH = os.path.join(ROOT, "models", "name_ngram.joblib")

MIN_TOKEN_LEN = 4          # surname-position token floor for rule 6
NEG_MIN_OCCURRENCES = 3
THRESHOLD = 0.95
C = 10.0


def segments(name: str):
    """Owner string -> clean segments (SURNAME FIRST MIDDLE strings)."""
    out = []
    for seg in re.split(r"\b(?:&|AND)\b", (name or "").upper()):
        toks = [t for t in re.sub(r"[^A-Z\s]", " ", seg).split() if t.isalpha()]
        if toks:
            out.append(" ".join(toks))
    return out


def main():
    d = json.load(open(DICT_PATH))
    dict_sur = set(d["hindu_last_names"]) | set(d["sikh_names"])
    excl = set(d["muslim_names"]) | set(d.get("christian_names", []))

    pos_seg, neg_seg = Counter(), Counter()
    for db, table in DBS:
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        for name, hindu in con.execute(
                f"SELECT owner_name, is_hindu FROM {table} "
                f"WHERE owner_name IS NOT NULL"):
            (pos_seg if hindu else neg_seg).update(segments(name))
        con.close()
    print(f"positive segments {sum(pos_seg.values()):,} "
          f"({len(pos_seg):,} types); negative {sum(neg_seg.values()):,} "
          f"({len(neg_seg):,} types)")

    # holdout by surname token: any segment whose surname-position token is
    # held out leaves training — simulates a dict-missing surname
    pos_sur = Counter()
    for s, n in pos_seg.items():
        pos_sur[s.split()[0]] += n
    random.seed(42)
    hold_cand = [t for t, n in pos_sur.items() if n >= 5 and t not in dict_sur]
    hold = set(random.sample(hold_cand, int(len(hold_cand) * 0.2)))

    X_pos = [s for s, n in pos_seg.items() if n >= 2 and s.split()[0] not in hold]
    X_neg = [s for s, n in neg_seg.items() if n >= NEG_MIN_OCCURRENCES]
    y = [1] * len(X_pos) + [0] * len(X_neg)
    w = ([1 + math.sqrt(pos_seg.get(s, 1) - 1) for s in X_pos]
         + [1 + math.sqrt(neg_seg.get(s, 1) - 1) for s in X_neg])

    model = Pipeline = None
    from sklearn.pipeline import make_pipeline
    model = make_pipeline(
        TfidfVectorizer(analyzer="char_wb", ngram_range=(3, 5),
                        lowercase=False, sublinear_tf=True, min_df=2),
        LogisticRegression(max_iter=2000, C=C))
    model.fit(X_pos + X_neg, y, logisticregression__sample_weight=w)

    def p(s):
        return model.predict_proba([s])[0][1]

    # self-audit 1: held-out surname segments (true rule-6 scenario)
    hold_segs = [f"{t} ARUN" for t in sorted(hold)] + \
                [f"{t} PRIYA" for t in sorted(hold)]
    hv = [p(s) for s in hold_segs]
    print(f"\nheld-out surname segments (n={len(hv)}): "
          f"median={statistics.median(hv):.3f} "
          f">=0.8: {sum(v >= 0.8 for v in hv) / len(hv):.2f}")
    # self-audit 2: western traps must collapse
    traps = ["JOHNSON MARY", "SMITH JAMES", "GARCIA MARIA", "MILLER ROBERT",
             "WILLIAMS JOHN", "NGUYEN THI", "WANG WEI", "TRAN MINH",
             "MARTINEZ CARLOS", "ANDERSON SARAH"]
    print("western traps:", {t: round(p(t), 3) for t in traps})

    os.makedirs(os.path.dirname(MODEL_PATH), exist_ok=True)
    joblib.dump({"model": model, "threshold": THRESHOLD,
                 "min_len": MIN_TOKEN_LEN}, MODEL_PATH)
    print(f"\nwrote {MODEL_PATH}")


if __name__ == "__main__":
    main()
