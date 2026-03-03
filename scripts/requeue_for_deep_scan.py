import pandas as pd
import os
import re
import sys
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.community_pipeline import SDATFormatter

def get_st(a):
    if not a or pd.isna(a): return ""
    # Use official formatter to get the search-ready street name
    formatted = SDATFormatter.format_address(str(a))
    return formatted['street_name']

def requeue_for_deep_scan():
    RESULTS_FILE = "data/Final_Owner_Results.xlsx"
    STREET_DB = "data/MD_street Names.xlsx"
    
    if not os.path.exists(RESULTS_FILE):
        print(f"❌ Results file not found: {RESULTS_FILE}")
        return

    print(f"📖 Reading existing results to identify streets for Deep Scan...")
    results_df = pd.read_excel(RESULTS_FILE)
    
    if 'address' not in results_df.columns or 'county' not in results_df.columns:
        print("❌ Required columns ('address', 'county') not found in results.")
        return

    # Extract unique street names that were already searched
    print("🧹 Extracting and normalizing street names...")
    results_df['Street'] = results_df['address'].apply(get_st)
    
    # We only care about unique street + county pairs
    deep_scan_list = results_df[['Street', 'county']].drop_duplicates()
    deep_scan_list = deep_scan_list[deep_scan_list['Street'].str.len() > 0]
    
    print(f"📊 Identified {len(deep_scan_list)} unique streets to re-check for missing pages.")

    # Load current queue
    if os.path.exists(STREET_DB):
        existing_queue = pd.read_excel(STREET_DB)
        # Ensure it has the right columns
        if 'Address' not in existing_queue.columns:
            existing_queue.rename(columns={existing_queue.columns[0]: 'Address'}, inplace=True)
        
        print(f"📖 Current queue has {len(existing_queue)} streets.")
        # Combine
        # Using 'Address' for consistency with expand_streets.py
        deep_scan_list.rename(columns={'Street': 'Address'}, inplace=True)
        combined = pd.concat([existing_queue, deep_scan_list], ignore_index=True)
    else:
        deep_scan_list.rename(columns={'Street': 'Address'}, inplace=True)
        combined = deep_scan_list

    # Deduplicate the new queue
    final_len = len(combined.drop_duplicates(subset=['Address', 'county']))
    combined = combined.drop_duplicates(subset=['Address', 'county'])
    
    print(f"📝 New queue size: {final_len} streets (Deep Scan items added).")
    
    # Save back to queue
    combined.to_excel(STREET_DB, index=False)
    print(f"✅ Ready! Run 'python3 scripts/bulk_street_search_final.py' to start the Deep Scan.")
    print("💡 The scraper will now automatically prowl through ALL result pages for these streets.")

if __name__ == "__main__":
    requeue_for_deep_scan()
