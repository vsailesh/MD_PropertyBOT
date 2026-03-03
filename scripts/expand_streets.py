import pandas as pd
import os
import sys
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.community_pipeline import CommunityAddressFetcher, SDATFormatter

def expand_street_names():
    FILE_PATH = "data/MD_street Names.xlsx"
    
    if os.path.exists(FILE_PATH):
        existing_df = pd.read_excel(FILE_PATH)
        print(f"📖 Existing addresses: {len(existing_df)}")
    else:
        existing_df = pd.DataFrame(columns=['Address', 'county'])
        print("🆕 Creating new street name database.")

    fetcher = CommunityAddressFetcher()
    results_file = "data/Final_Owner_Results.xlsx"
    searched_streets = set()
    
    # 1. Load already searched streets from results
    if os.path.exists(results_file):
        print(f"📖 Loading searched streets from {results_file}...")
        try:
            results_df = pd.read_excel(results_file)
            if 'address' in results_df.columns:
                def get_st_norm(a):
                    if not a or pd.isna(a): return ""
                    return SDATFormatter.format_address(str(a))['street_name']
                
                searched_streets = set(results_df['address'].apply(get_st_norm).unique())
                searched_streets.discard("")
                print(f"📊 Found {len(searched_streets)} unique normalized streets already processed.")
        except Exception as e:
            print(f"⚠️ Warning: Could not load results file for filtering: {e}")

    # Comprehensive list of all 24 Maryland jurisdictions (23 counties + 1 city)
    counties = [
        "Allegany", "Anne Arundel", "Baltimore City", "Baltimore County", 
        "Calvert", "Caroline", "Carroll", "Cecil", "Charles", "Dorchester", 
        "Frederick", "Garrett", "Harford", "Howard", "Kent", "Montgomery", 
        "Prince George's", "Queen Anne's", "Saint Mary's", "Somerset", 
        "Talbot", "Washington", "Wicomico", "Worcester"
    ]
    
    import time
    new_data = []
    
    for county in counties:
        # Normalize county name for Osm/Display
        display_name = county if "County" in county or "City" in county else f"{county} County"
        print(f"\n🌍 Thorough Sweep: {display_name}...")
        
        try:
            # Fetch ALL unique street names for the entire jurisdiction
            streets = fetcher.fetch_county_streets(county)
            
            filtered_count = 0
            added_in_this_jurisdiction = 0
            
            for st_name in streets:
                # Extract normalized street name for duplicate check
                st_normalized = SDATFormatter.format_address(st_name)['street_name']
                
                if not st_normalized or st_normalized in searched_streets:
                    filtered_count += 1
                    continue
                    
                new_data.append({
                    'Address': st_normalized,
                    'county': display_name
                })
                added_in_this_jurisdiction += 1
            
            print(f"📊 {display_name}: Added {added_in_this_jurisdiction} unique streets, Filtered {filtered_count} existing/redundant.")
            
            # Mandated sleep between jurisdictions to avoid platform pressure
            if county != counties[-1]:
                wait_time = 15
                print(f"⏳ Cooling down for {wait_time}s...")
                time.sleep(wait_time)
                
        except Exception as e:
            print(f"❌ Error fetching for {county}: {e}")
            time.sleep(10)

    if not new_data:
        print("⚠️ No new addresses found.")
        return

    new_df = pd.DataFrame(new_data)
    
    # Combine
    combined_df = pd.concat([existing_df, new_df], ignore_index=True)
    
    # Standardize for deduplication
    combined_df['Address'] = combined_df['Address'].str.upper().str.strip()
    
    # Remove duplicates
    original_len = len(combined_df)
    combined_df = combined_df.drop_duplicates(subset=['Address', 'county'])
    final_len = len(combined_df)
    
    print(f"\n📊 Deduplication: {original_len} -> {final_len} ({original_len - final_len} duplicates removed)")
    
    # Export
    combined_df.to_excel(FILE_PATH, index=False, engine='openpyxl')
    print(f"✅ Updated {FILE_PATH} with {final_len} total entries.")

if __name__ == "__main__":
    expand_street_names()
