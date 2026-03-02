import pandas as pd
import time
import os
import re
import sys
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.community_pipeline import SDATAutoScraper, RaceEthnicityPredictor

def run_final_bulk_search():
    INPUT_FILE = "data/MD_street Names.xlsx"
    OUTPUT_FILE = "data/Final_Owner_Results.xlsx"
    
    if not os.path.exists(INPUT_FILE):
        print(f"❌ Error: {INPUT_FILE} not found.")
        return

    print(f"\n{'='*70}")
    print(f"🚀 FINAL BULK STREET SEARCH")
    print(f"📂 Source: {INPUT_FILE}")
    print(f"{'='*70}")

    df = pd.read_excel(INPUT_FILE)
    
    from src.community_pipeline import SDATFormatter
    
    # Helper to clean/extract street name
    def extract_street_name(addr):
        if not addr or pd.isna(addr): return ""
        formatted = SDATFormatter.format_address(str(addr))
        return formatted['street_name']

    # Get unique combinations of street and county
    df['temp_street'] = df['Address'].apply(extract_street_name)
    unique_targets = df[['temp_street', 'county']].drop_duplicates().values.tolist()
    
    # Load existing results to avoid duplicates and allow resuming
    existing_results = []
    if os.path.exists(OUTPUT_FILE):
        print(f"📖 Loading existing results from {OUTPUT_FILE}...")
        try:
            existing_df = pd.read_excel(OUTPUT_FILE)
            existing_results = existing_df.to_dict('records')
            print(f"📊 Found {len(existing_results)} existing records.")
        except Exception as e:
            print(f"⚠️ Warning: Could not load existing results: {e}")

    print(f"📊 Identified {len(unique_targets)} unique street/county pairs to search.")

    scraper = SDATAutoScraper(headless=True)
    predictor = RaceEthnicityPredictor()
    all_new_results = []
    
    # Combined results for saving
    def save_results(new_batch, existing):
        combined = existing + new_batch
        if combined:
            final_df = pd.DataFrame(combined)
            # Remove any duplicates in results
            final_df = final_df.drop_duplicates(subset=['owner_name', 'address', 'county'])
            final_df.to_excel(OUTPUT_FILE, index=False)
            return len(final_df)
        return 0

    try:
        scraper.start_driver()
        
        for i, (street, county) in enumerate(unique_targets, 1):
            if not street or street == 'UNKNOWN': continue
            
            print(f"\n🏘️  [{i}/{len(unique_targets)}] Searching: {street} in {county}")
            
            try:
                # Use the scraper method we added earlier
                results = scraper.search_street_bulk(street, county)
                
                if results:
                    print(f"  ✅ Found {len(results)} properties")
                    # Add race prediction to each result
                    for res in results:
                        prediction = predictor.predict_race(res['owner_name'])
                        res.update({
                            'predicted_race': prediction['predicted_race'],
                            'race_confidence': prediction['confidence'],
                            'race_method': prediction['method']
                        })
                    all_new_results.extend(results)
                else:
                    print(f"  ❌ No results found on SDAT.")

            except Exception as e:
                print(f"  ⚠️ Error searching street {street}: {e}")
                # Optional: Restart driver if it crashes
                if "session" in str(e).lower() or "driver" in str(e).lower():
                    print("  🔄 Restarting WebDriver...")
                    scraper.stop_driver()
                    scraper.start_driver()

            # Periodic save to avoid data loss (every 5 streets)
            if i % 5 == 0:
                total = save_results(all_new_results, existing_results)
                print(f"💾 Interim results saved. Total records in {OUTPUT_FILE}: {total}")

    except KeyboardInterrupt:
        print("\n🛑 Interrupted. Saving current results...")
    except Exception as e:
        print(f"\n💥 Error: {e}")
        import traceback
        traceback.print_exc()
    finally:
        scraper.stop_driver()
        total = save_results(all_new_results, existing_results)
        print(f"\n{'='*70}")
        print(f"✅ FINAL SEARCH COMPLETE")
        print(f"💾 Results saved to: {OUTPUT_FILE}")
        print(f"📊 Total Properties Found: {total}")
        print(f"{'='*70}")

if __name__ == "__main__":
    run_final_bulk_search()
