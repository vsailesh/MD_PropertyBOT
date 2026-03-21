#!/usr/bin/env python3
import os
import sqlite3
import pandas as pd
import sys
import re

# Add project root to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from src.street_name_cleaner import StreetNameCleaner

def analyze_coverage():
    db_path = "data/property_search.db"
    master_streets_path = "data/MD_street_Names.xlsx"
    
    if not os.path.exists(db_path):
        print(f"❌ Database not found at {db_path}")
        return
    if not os.path.exists(master_streets_path):
        print(f"❌ Master list not found at {master_streets_path}")
        return

    cleaner = StreetNameCleaner()
    
    # 1. Load Master List
    print(f"📖 Loading master list: {master_streets_path}...")
    df_master = pd.read_excel(master_streets_path)
    
    # 2. Load Search Progress
    print("📖 Loading search progress from database...")
    from src.robust_database import PropertyDatabase
    db = PropertyDatabase()
    conn = sqlite3.connect(db_path)
    df_progress = pd.read_sql_query("SELECT street_name, county, status, properties_found FROM search_progress", conn)
    
    # Normalize progress keys using the production logic
    df_progress['norm_county'] = df_progress['county'].apply(lambda x: db.normalize_county(x).upper())
    df_progress['norm_street'] = df_progress['street_name'].str.strip().str.upper()
    existing_searches = set(zip(df_progress['norm_county'], df_progress['norm_street']))
    
    # 3. Clean and Analyze Master List
    print("🧹 Cleaning master list names...")
    df_master['cleaned_street'] = df_master['Address'].apply(cleaner.clean_street_name)
    df_master['norm_county'] = df_master['county'].apply(lambda x: db.normalize_county(x).upper())
    
    # Filter out junk (single letters, very short)
    def is_junk(name):
        if not name: return True
        if len(name) <= 1: return True
        if name.isdigit(): return True
        return False
        
    df_master['is_junk'] = df_master['cleaned_street'].apply(is_junk)
    df_master_clean = df_master[~df_master['is_junk']].copy()
    
    # Deduplicate master list by (county, cleaned_street)
    df_master_unique = df_master_clean.drop_duplicates(subset=['norm_county', 'cleaned_street'])
    
    print(f"📊 Original master rows: {len(df_master):,}")
    print(f"📊 After junk filter: {len(df_master_clean):,}")
    print(f"📊 Unique SDAT-style streets per county: {len(df_master_unique):,}")

    # 4. Find Missing
    missing_rows = []
    for _, row in df_master_unique.iterrows():
        key = (row['norm_county'], row['cleaned_street'])
        if key not in existing_searches:
            missing_rows.append(row)
            
    df_missing = pd.DataFrame(missing_rows)
    print(f"📍 FOUND {len(df_missing):,} STREETS NEVER SEARCHED")
    
    # 5. Identify Capped Searches (>= 5000 results in SDAT usually means truncated)
    # Actually, the user's DB has massive numbers like 90k for 'P'. 
    # That suggests the scraper isn't just searching "P", but maybe it's aggregating results.
    # However, SDAT standard capping is at 5000 records.
    capping_threshold = 5000
    df_capped = df_progress[df_progress['properties_found'] >= capping_threshold]
    print(f"⚠️ {len(df_capped):,} searches hit the 5,000 record cap and may be incomplete.")

    # 6. Export Results
    strict_path = "data/MD_street_Names_Strict.xlsx"
    missing_path = "data/MD_street_Names_Leftout.xlsx"
    
    df_master_unique[['Address', 'county', 'cleaned_street']].to_excel(strict_path, index=False)
    print(f"✅ Exported clean master list: {strict_path}")
    
    if len(df_missing) > 0:
        df_missing[['Address', 'county', 'cleaned_street']].to_excel(missing_path, index=False)
        print(f"✅ Exported leftout streets: {missing_path}")
        
    # 7. Failed Search Retry List
    df_failed = df_progress[df_progress['status'] == 'failed']
    if len(df_failed) > 0:
        failed_path = "data/MD_street_Names_Failed_Retry.xlsx"
        df_failed[['street_name', 'county']].to_excel(failed_path, index=False)
        print(f"✅ Exported failed retry list: {failed_path}")

if __name__ == "__main__":
    analyze_coverage()
