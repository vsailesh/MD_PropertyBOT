import pandas as pd
import os
import sys
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.community_pipeline import RaceEthnicityPredictor

def extract_hindu_owners(input_file, output_file):
    if not os.path.exists(input_file):
        print(f"❌ Input file not found: {input_file}")
        return

    print(f"📖 Reading {input_file}...")
    df = pd.read_excel(input_file)
    
    # Initialize predictor
    predictor = RaceEthnicityPredictor()
    
    print("🧠 Filtering for Hindu names...")
    results = []
    
    # Process each row
    for index, row in df.iterrows():
        owner_name = row.get('owner_name')
        if not owner_name or owner_name == 'Not Found':
            continue
            
        prediction = predictor.predict_race(owner_name)
        
        # Check for Hindu identification
        if prediction.get('is_hindu') is True:
            # Create a clean record
            record = row.to_dict()
            record['predicted_race'] = prediction['predicted_race']
            record['sub_category'] = prediction.get('sub_category', 'Hindu')
            record['race_confidence'] = prediction['confidence']
            record['race_method'] = prediction['method']
            results.append(record)
    
    if not results:
        print("⚠️ No Hindu owners identified.")
        return
        
    hindu_df = pd.DataFrame(results)
    
    # Sort by confidence
    hindu_df = hindu_df.sort_values(by='race_confidence', ascending=False)
    
    print(f"✅ Found {len(hindu_df)} Hindu owners.")
    
    # Export to Excel
    hindu_df.to_excel(output_file, index=False, engine='openpyxl')
    print(f"📊 Exported to {output_file}")

if __name__ == "__main__":
    input_xlsx = "data/Final_Owner_Results.xlsx"
    output_xlsx = "data/Hindu_Origin_Owners.xlsx"
    extract_hindu_owners(input_xlsx, output_xlsx)
