import pandas as pd
import os
import sys
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.community_pipeline import CommunityAddressFetcher

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
                # Helper to clean/extract street name
                import re
                def get_st(a):
                    if not a or pd.isna(a): return ""
                    s = str(a).strip().upper()
                    name = re.sub(r'^\d+\s+', '', s)
                    return name.strip()
                
                searched_streets = set(results_df['address'].apply(get_st).unique())
                print(f"📊 Found {len(searched_streets)} unique streets already processed.")
        except Exception as e:
            print(f"⚠️ Warning: Could not load results file for filtering: {e}")

    # Target areas with coordinates
    targets = [
        # Howard County
        ("Columbia", 39.2156, -76.8582, "Howard"),
        ("Ellicott City", 39.2673, -76.7983, "Howard"),
        # Anne Arundel County
        ("Annapolis", 38.9784, -76.4922, "Anne Arundel"),
        ("Glen Burnie", 39.1632, -76.6172, "Anne Arundel"),
        # Prince George's County
        ("Bowie", 39.0068, -76.7791, "Prince George's"),
        ("Laurel", 39.0993, -76.8483, "Prince George's"),
        ("Greenbelt", 39.0046, -76.8755, "Prince George's"),
        # Montgomery County
        ("Silver Spring", 38.9907, -77.0261, "Montgomery"),
        ("Bethesda", 38.9847, -77.0947, "Montgomery"),
        ("Germantown", 39.1732, -77.2717, "Montgomery"),
        ("Rockville", 39.0840, -77.1528, "Montgomery"),
        ("Gaithersburg", 39.1434, -77.2014, "Montgomery"),
        # Frederick County
        ("Frederick", 39.4143, -77.4105, "Frederick"),
        # Charles County
        ("Waldorf", 38.6246, -76.8822, "Charles"),
        # Harford County
        ("Bel Air", 39.5359, -76.3483, "Harford"),
        # Baltimore County
        ("Towson", 39.4015, -76.6019, "Baltimore County"),
        # More Howard/AA
        ("Elkridge", 39.2140, -76.7083, "Howard"),
        ("Odenton", 39.1026, -76.6997, "Anne Arundel"),
        ("Severna Park", 39.0837, -76.5508, "Anne Arundel")
    ]
    
    import time
    new_data = []
    
    for name, lat, lon, county in targets:
        print(f"\n🏘️ Fetching from {name}, {county}...")
        try:
            # Fetch up to 500 random addresses within 2 miles of the center
            # Limit of 500 ensures we get a broad set while staying safe
            addresses = fetcher.fetch_radius_addresses(lat, lon, radius_miles=2.0, limit=500)
            
            import re
            def get_st_internal(a):
                s = str(a).strip().upper()
                return re.sub(r'^\d+\s+', '', s).strip()

            filtered_count = 0
            for addr in addresses:
                st_name = get_st_internal(addr['address'])
                if st_name in searched_streets:
                    filtered_count += 1
                    continue
                    
                new_data.append({
                    'Address': addr['address'],
                    'county': county # Use the target county as provisional
                })
            
            if filtered_count > 0:
                print(f"🛡️  Filtered {filtered_count} addresses already present in results.")
            
            # Mandated sleep between targets to avoid rate limiting
            if name != targets[-1][0]:
                print(f"⏳ Cooling down for 5s...")
                time.sleep(5)
                
        except Exception as e:
            print(f"❌ Error fetching for {name}: {e}")
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
