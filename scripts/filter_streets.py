import os
import sys
import pandas as pd
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.community_pipeline import SDATFormatter

def extract_street_name(addr):
    if not addr or pd.isna(addr): return ""
    # Use the official SDATFormatter for exact consistency with the search engine
    formatted = SDATFormatter.format_address(str(addr))
    return formatted['street_name']

def filter_streets():
    STREET_DB = "data/MD_street Names.xlsx"
    RESULTS_FILE = "data/Final_Owner_Results.xlsx"
    
    if not os.path.exists(STREET_DB):
        print(f"❌ Error: {STREET_DB} not found.")
        return

    print(f"📖 Reading street database: {STREET_DB}")
    db_df = pd.read_excel(STREET_DB)
    initial_total = len(db_df)
    
    # Step 1: Clean all addresses using SDATFormatter (strips numbers, suffixes, directions)
    print(f"🧹 Cleaning {initial_total} addresses with strict SDAT rules...")
    db_df['Street'] = db_df['Address'].apply(extract_street_name)
    
    # Show some before/after examples
    changed = db_df[db_df['Address'] != db_df['Street']].head(10)
    if len(changed) > 0:
        print(f"   Examples of cleaned addresses:")
        for _, row in changed.iterrows():
            print(f"     '{row['Address']}' → '{row['Street']}'")
    
    # Step 2: Remove empty/blank entries
    db_df = db_df[db_df['Street'].str.strip().str.len() > 0]
    empty_removed = initial_total - len(db_df)
    if empty_removed > 0:
        print(f"🗑️  Removed {empty_removed} entries that became empty after cleaning.")
    
    # Step 3: Filter out streets already in results
    if os.path.exists(RESULTS_FILE):
        print(f"📖 Reading processed results: {RESULTS_FILE}")
        results_df = pd.read_excel(RESULTS_FILE)
        
        if 'address' in results_df.columns:
            searched_streets = set(results_df['address'].apply(extract_street_name).unique())
            searched_streets.discard("")  # Remove empty from set
            print(f"📊 Found {len(searched_streets)} unique streets already processed.")
            
            before_filter = len(db_df)
            db_df = db_df[~db_df['Street'].isin(searched_streets)]
            removed = before_filter - len(db_df)
            print(f"✂️ Removed {removed} streets that were already searched.")
        else:
            print("⚠️ 'address' column not found in results file. Skipping filtering.")
    else:
        print(f"⚠️ {RESULTS_FILE} not found. Only normalizing street database.")

    # Step 4: Drop duplicates
    before_dedup = len(db_df)
    db_df = db_df[['Street', 'county']].drop_duplicates()
    dedup_removed = before_dedup - len(db_df)
    if dedup_removed > 0:
        print(f"🔄 Removed {dedup_removed} duplicates after cleaning.")
    
    # Rename back to 'Address' for downstream compatibility
    db_df.rename(columns={'Street': 'Address'}, inplace=True)
    
    print(f"✅ Final unique streets to search: {len(db_df)}")
    
    # Save back
    db_df.to_excel(STREET_DB, index=False, engine='openpyxl')
    print(f"💾 Updated {STREET_DB}")

if __name__ == "__main__":
    filter_streets()
