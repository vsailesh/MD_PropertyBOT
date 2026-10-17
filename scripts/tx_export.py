#!/usr/bin/env python3
"""Export hindu-flagged TX owners to Excel (dashboard format, TX flavor).

tx_properties.situs_display = "4707 INTERLACHEN CIR \r\nDALLAS, TX 75287"
-> split into Street / City / State / Zip. No coordinates in the source
(geoId is a parcel id, not lat/lon) — leave geo columns for a later
geocode pass if the dashboard needs a TX map.

Usage: python scripts/tx_export.py [-o data/TX_Hindu_Owners.xlsx] [--all]
"""
import argparse
import os
import re
import sqlite3
import sys

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from geocode_results import extract_surname, map_surname_to_origin

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(ROOT, "data", "tx_properties.db")

# multiline tail: "MCKINNEY, TX 75069" (street on its own line)
TAIL_RE = re.compile(r"^([^,]+),\s*([A-Z]{2})\s+(\d{5}(?:-\d{4})?)\s*$")
# single line, commas throughout: "325 RD 6424, PALESTINE, TX 75801"
ONELINE_RE = re.compile(
    r"^(.*?),\s*([^,]+),\s*([A-Z]{2})\s+(\d{5}(?:-\d{4})?)\s*$")


def parse_situs(s):
    """situsDisplay -> (street, city, state, zip).

    Two shapes in the wild:
      '401 CHRISTIAN ST \\r\\nMCKINNEY, TX 75069'   (newline before city)
      '325 PRIVATE ROAD 6424, PALESTINE, TX 75801'  (all one line)
    Don't fold newlines first — that merges street+city into one
    comma-less blob and kills the tail match.
    """
    if not s:
        return "", "", "", ""
    lines = [ln.strip() for ln in s.replace("\r", "").split("\n") if ln.strip()]
    if not lines:
        return "", "", "", ""
    if len(lines) >= 2:
        m = TAIL_RE.match(lines[-1])
        if m:
            street = ", ".join(lines[:-1])
            return street, m.group(1).strip(), m.group(2), m.group(3)
        return ", ".join(lines), "", "", ""
    m = ONELINE_RE.match(lines[0])
    if m:
        return m.group(1).strip(), m.group(2).strip(), m.group(3), m.group(4)
    return lines[0], "", "", ""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("-o", "--output", default=os.path.join(ROOT, "data", "TX_Hindu_Owners.xlsx"))
    ap.add_argument("--all", action="store_true", help="all rows, not just hindu-flagged")
    args = ap.parse_args()

    con = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    where = "" if args.all else "WHERE is_hindu = 1"
    df = pd.read_sql_query(
        f"SELECT county, owner_name, doing_business_as, situs_display, "
        f"market_value, geo_id, is_hindu, category, last_seen "
        f"FROM tx_properties {where}", con)
    con.close()
    if df.empty:
        sys.exit("No matching rows in tx_properties")

    parsed = df["situs_display"].apply(parse_situs)
    surnames = df["owner_name"].apply(extract_surname)
    origins = surnames.apply(map_surname_to_origin)
    out = pd.DataFrame({
        "County": df["county"],
        "Owner Name": df["owner_name"],
        "Street": parsed.apply(lambda t: t[0]),
        "City": parsed.apply(lambda t: t[1]),
        "State": parsed.apply(lambda t: t[2]),
        "Zip Code": parsed.apply(lambda t: t[3]),
        "Market Value": df["market_value"],
        "Parcel (geoId)": df["geo_id"],
        "Surname": surnames,
        "Region": origins.apply(lambda o: o["region"]),
        "Countries": origins.apply(lambda o: o["countries"]),
        "Origin_Confidence": origins.apply(lambda o: o["confidence"]),
        "Origin_Group": origins.apply(lambda o: o["group"]),
        "Category": df["category"],
        "Source": "countytaxrates.com",
        "Last Seen": df["last_seen"],
    })

    out.to_excel(args.output, index=False)
    print(f"{len(out):,} rows -> {args.output}")


if __name__ == "__main__":
    main()
