import pandas as pd
import sqlite3
import os

def sync_new_hindus():
    db_path = "data/property_search.db"
    
    # We use temp_mapped.xlsx because the geocoder is secretly outputting there, but 
    # the user also sees Hindu_Origin_Owners_Mapped.xlsx
    
    # If temp_mapped.xlsx exists, that is the most authoritative active file.
    excel_path = "data/temp_mapped.xlsx"
    if not os.path.exists(excel_path):
        excel_path = "data/Hindu_Origin_Owners_Mapped.xlsx"
        if not os.path.exists(excel_path):
            print("Target Excel not found.")
            return

    print(f"Reading target excel: {excel_path}...")
    df_excel = pd.read_excel(excel_path)

    print("Connecting to database...")
    conn = sqlite3.connect(db_path)
    df_db = pd.read_sql_query("SELECT id, street_name, county, owner_name, address, city, state, zip_code, predicted_race, race_confidence, race_method, is_hindu, sub_category, latitude, longitude FROM properties WHERE is_hindu = 1", conn)
    conn.close()

    # Find missing rows from DB that aren't in Excel
    missing = df_db[~df_db['id'].isin(df_excel['id'])]
    
    if len(missing) > 0:
        print(f"Found {len(missing)} wildly new extracted properties to sync.")
        
        # Ensure columns match to prevent Pandas errors
        for col in df_excel.columns:
            if col not in missing.columns:
                missing[col] = None
                
        # Append seamlessly
        updated_df = pd.concat([df_excel, missing], ignore_index=True)
        
        # Save back safely
        updated_df.to_excel(excel_path, index=False)
        print(f"Successfully injected {len(missing)} records into {excel_path}. Geocoder will pick them up automatically on its next restart cycle!")
    else:
        print("Dataset is fully synchronized. No new missing rows.")

if __name__ == "__main__":
    sync_new_hindus()
