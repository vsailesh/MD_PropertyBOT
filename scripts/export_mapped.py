#!/usr/bin/env python3
"""
Export geocoded Hindu owners from SQLite into the dashboard's Mapped.xlsx format.

Replaces the old chain: export -> geocode_results.py (Excel double-pass).
Coordinates now come from the single DB-side pass (geocode_database.py);
surname/origin enrichment is computed here from owner names.
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd

from geocode_results import extract_surname, map_surname_to_origin
from src.robust_database import PropertyDatabase


def main():
    parser = argparse.ArgumentParser(description='Export Hindu owners (geocoded) for the dashboard')
    parser.add_argument('--db', default='data/property_search.db')
    parser.add_argument('-o', '--output', default='data/Hindu_Origin_Owners_Mapped.xlsx')
    parser.add_argument('--all', action='store_true', help='Export all owners, not just Hindu')
    args = parser.parse_args()

    db = PropertyDatabase(args.db)
    filters = {} if args.all else {'is_hindu': True}
    df = db.get_properties(**filters)
    if df.empty:
        sys.exit("No matching properties in database")

    total = len(df)
    missing = int(df['latitude'].isna().sum()) if 'latitude' in df.columns else total
    print(f"📊 {total:,} rows | {total - missing:,} geocoded | {missing:,} missing coordinates "
          f"(run scripts/geocode_database.py first to fill them)")

    # Surname / origin enrichment (pure dict lookups, no API calls)
    surnames = df['owner_name'].apply(extract_surname)
    origins = surnames.apply(map_surname_to_origin)
    out = pd.DataFrame({
        'Street': df.get('street_name'),
        'County': df.get('county'),
        'Owner Name': df.get('owner_name'),
        'Address': df.get('address'),
        'City': df.get('city'),
        'State': df.get('state'),
        'Zip Code': df.get('zip_code'),
        'Latitude': df.get('latitude'),
        'Longitude': df.get('longitude'),
        'Geo_Address': df.get('geo_address'),
        'Method': df.get('geo_method'),
        'Surname': surnames,
        'Region': origins.apply(lambda o: o['region']),
        'Countries': origins.apply(lambda o: o['countries']),
        'Origin_Confidence': origins.apply(lambda o: o['confidence']),
        'Origin_Group': origins.apply(lambda o: o['group']),
        'Predicted Race': df.get('predicted_race'),
        'Sub-Category': df.get('sub_category'),
    })

    out.to_excel(args.output, index=False)
    print(f"✅ Wrote {len(out):,} rows to {args.output}")


if __name__ == "__main__":
    main()
