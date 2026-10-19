#!/usr/bin/env python3
"""Export NJ hindu owners to data/NJ_Hindu_Owners.xlsx (TX-format sibling).

Only is_hindu rows go to the export; the DB keeps everything (user
direction 2026-10-19).

Usage: python scripts/nj_export.py
"""
import os
import re
import sqlite3
import sys

import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
sys.path.insert(0, ROOT)

DB_PATH = os.path.join(ROOT, "data", "nj_properties.db")
OUT = os.path.join(ROOT, "data", "NJ_Hindu_Owners.xlsx")

from geocode_results import extract_surname, map_surname_to_origin  # noqa: E402


def split_csz(csz):
    """'EDISON, NJ 08817' -> (city, state, zip)"""
    m = re.match(r"^(.*?),\s*([A-Z]{2})\s+(\d{5}(?:-\d{4})?)\s*$", (csz or "").strip())
    if m:
        return m.group(1).title(), m.group(2), m.group(3)
    return (csz or "").title(), "", ""


def main():
    con = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    df = pd.read_sql_query(
        "SELECT county, district, block, lot, qual, owner_name, mailing_address,"
        " city_state_zip, property_location, prop_class, total_assmnt, taxes,"
        " sale_date, sale_price, is_hindu, category, latitude, longitude, geo_method"
        " FROM nj_properties WHERE is_hindu=1", con)
    con.close()

    city = df["city_state_zip"].apply(split_csz)
    surnames = df["owner_name"].apply(extract_surname)
    origins = surnames.apply(map_surname_to_origin)

    out = pd.DataFrame({
        "County": df["county"].str.title(),
        "Municipality": df["district"].str.title(),
        "Block": df["block"],
        "Lot": df["lot"],
        "Owner Name": df["owner_name"],
        "Property Address": df["property_location"].str.title(),
        "Mailing Address": df["mailing_address"].str.title(),
        "City": city.apply(lambda t: t[0]),
        "State": city.apply(lambda t: t[1]),
        "Zip Code": city.apply(lambda t: t[2]),
        "Property Class": df["prop_class"],
        "Total Assessment": df["total_assmnt"],
        "Taxes": df["taxes"],
        "Sale Date": df["sale_date"],
        "Sale Price": df["sale_price"],
        "Latitude": df["latitude"],
        "Longitude": df["longitude"],
        "Method": df["geo_method"],
        "Surname": surnames,
        "Region": origins.apply(lambda o: o["region"]),
        "Countries": origins.apply(lambda o: o["countries"]),
    })
    out.to_excel(OUT, index=False)
    print(f"{len(out):,} rows -> {OUT}")


if __name__ == "__main__":
    main()
