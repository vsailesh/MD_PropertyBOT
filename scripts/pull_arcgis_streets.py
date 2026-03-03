import pandas as pd
import os
import sys
import requests
import io

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.community_pipeline import SDATFormatter

def pull_arcgis_streets():
    CSV_URL = "https://data-maryland.opendata.arcgis.com/datasets/maryland::maryland-roadway-names.csv?where=1=1"
    STREET_DB = "data/MD_street Names.xlsx"
    RESULTS_FILE = "data/Final_Owner_Results.xlsx"
    
    print(f"🌐 Downloading ArcGIS Roadway Names dataset...")
    try:
        response = requests.get(CSV_URL, timeout=60)
        response.raise_for_status()
        df = pd.read_csv(io.StringIO(response.text))
        print(f"✅ Downloaded {len(df)} raw roadway entries.")
    except Exception as e:
        print(f"❌ Error downloading data: {e}")
        return

    # Identify relevant columns
    # Based on sample: ROAD_NAME (index 3), COUNTY_NAME (index 19)
    # We'll use names for robustness if possible, or positional if necessary
    road_col = 'ROAD_NAME'
    county_col = 'COUNTY_NAME'
    
    if road_col not in df.columns or county_col not in df.columns:
        print(f"⚠️ Column names mismatch. Available: {df.columns.tolist()[:5]}...")
        # Fallback to positional if standard names aren't found
        # (Assuming the structure matches the header we saw)
        # road_col = df.columns[3]
        # county_col = df.columns[19]
        return

    print(f"🧹 Processing and normalizing {len(df)} streets...")
    
    # 1. Basic cleaning
    df = df.dropna(subset=[road_col, county_col])
    
    # 2. Load already searched streets from results for filtering
    searched_streets = set()
    if os.path.exists(RESULTS_FILE):
        print(f"📖 Loading searched streets for filtering...")
        try:
            results_df = pd.read_excel(RESULTS_FILE)
            if 'address' in results_df.columns:
                def get_st_norm(a):
                    if not a or pd.isna(a): return ""
                    return SDATFormatter.format_address(str(a))['street_name']
                searched_streets = set(results_df['address'].apply(get_st_norm).unique())
                searched_streets.discard("")
                print(f"📊 Filtering against {len(searched_streets)} already processed streets.")
        except Exception as e:
            print(f"⚠️ Warning: Could not load results file: {e}")

    # 3. Normalize and Filter in one pass
    new_data = []
    seen_in_this_batch = set()
    
    for _, row in df.iterrows():
        raw_name = str(row[road_col])
        raw_county = str(row[county_col])
        
        # Normalize street name using SDAT engine
        formatted = SDATFormatter.format_address(raw_name)
        st_normalized = formatted['street_name']
        
        if not st_normalized or st_normalized in searched_streets:
            continue
            
        # Deduplicate within this batch
        key = (st_normalized, raw_county)
        if key in seen_in_this_batch:
            continue
            
        new_data.append({
            'Address': st_normalized,
            'county': raw_county
        })
        seen_in_this_batch.add(key)

    if not new_data:
        print("⚠️ No new unique streets found in this dataset.")
        return

    new_df = pd.DataFrame(new_data)
    print(f"✨ Found {len(new_df)} unique, new search targets.")

    # 4. Merge with existing database
    if os.path.exists(STREET_DB):
        existing_df = pd.read_excel(STREET_DB)
        print(f"📖 Existing database entries: {len(existing_df)}")
        combined_df = pd.concat([existing_df, new_df], ignore_index=True)
    else:
        combined_df = new_df

    # Final deduplication
    original_len = len(combined_df)
    combined_df['Address'] = combined_df['Address'].str.upper().str.strip()
    combined_df = combined_df.drop_duplicates(subset=['Address', 'county'])
    final_len = len(combined_df)
    
    print(f"📊 Final Database: {original_len} -> {final_len} ({original_len - final_len} duplicates removed)")
    
    # Export
    os.makedirs(os.path.dirname(STREET_DB), exist_ok=True)
    combined_df.to_excel(STREET_DB, index=False, engine='openpyxl')
    print(f"✅ Updated {STREET_DB} with {final_len} total entries.")

if __name__ == "__main__":
    pull_arcgis_streets()
