import sqlite3
import pandas as pd
import time
import sys
import os

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from src.community_pipeline import RaceEthnicityPredictor

def re_evaluate():
    db_path = "data/property_search.db"
    
    if not os.path.exists(db_path):
        print("Database not found!")
        return
        
    predictor = RaceEthnicityPredictor()
    # Disable ethnicolr since it's an LSTM that takes hours over 1.6M records.
    # The original DB already ran ethnicolr. We only want to apply the NEW 
    # dictionary-based Hindu/Sikh inclusions and false-positive exclusions.
    predictor.use_ethnicolr = False
    print("✅ Disabled ethnicolr for ultra-fast retroactive scanning (local dict only)")
    
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    
    print("Reading all properties from database...")
    cur.execute("SELECT id, owner_name, is_hindu, predicted_race, race_confidence, race_method FROM properties WHERE owner_name IS NOT NULL AND owner_name != 'Not Found' AND owner_name != ''")
    rows = cur.fetchall()
    
    print(f"Total valid properties to verify: {len(rows)}")
    
    updates = []
    new_hindus = 0
    removed_hindus = 0
    
    start_time = time.time()
    
    for i, (row_id, owner_name, old_is_hindu, old_pred_race, old_conf, old_meth) in enumerate(rows):
        res = predictor.predict_race(owner_name)
        
        # Look for explicit changes
        new_is_hindu = 1 if res.get('is_hindu') else 0
        
        # If the local dict explicitly finds a match (True) OR explicitly kills a false positive
        if new_is_hindu != old_is_hindu:
            # Check if this new result is actually a strong signal from the dict logic
            if res['method'] == 'Local Dictionary':
                if new_is_hindu == 1:
                    new_hindus += 1
                else:
                    removed_hindus += 1
                    
                updates.append((
                    res['predicted_race'],
                    res['confidence'],
                    res['method'],
                    new_is_hindu,
                    res.get('sub_category', ''),
                    row_id
                ))
            
        if (i+1) % 100000 == 0:
            print(f"Processed {i+1}/{len(rows)}... Found {new_hindus} new Muslims/Christians removed, added {new_hindus} new Sikhs/Hindus.")
            
    print(f"\nProcessing complete in {time.time() - start_time:.1f}s.")
    print(f"Total New Hindu/Sikhs Extracted: {new_hindus}")
    print(f"Total False-Positives Removed: {removed_hindus}")
    
    if updates:
        print("Updating database...")
        cur.executemany("""
            UPDATE properties
            SET predicted_race = ?,
                race_confidence = ?,
                race_method = ?,
                is_hindu = ?,
                sub_category = ?
            WHERE id = ?
        """, updates)
        conn.commit()
        print("Database updated!")
        print("Note: Excel export bypassed to protect the ongoing background geocoder's mapped coordinates.")
    else:
        print("No changes required to the database.")
        
    conn.close()

if __name__ == "__main__":
    re_evaluate()
