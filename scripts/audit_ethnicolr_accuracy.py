import pandas as pd
import sys
import os
import re

# Add src to path
sys.path.append(os.path.abspath('src'))
from community_pipeline import RaceEthnicityPredictor

def run_accuracy_audit(input_file='data/Hindu_Origin_Owners_Mapped.xlsx'):
    print(f"🕵️ Starting Accuracy Audit on {input_file}...")
    
    if not os.path.exists(input_file):
        print(f"❌ Error: {input_file} not found!")
        return

    df = pd.read_excel(input_file)
    predictor = RaceEthnicityPredictor()
    
    # 1. Audit Sample (Top 50 + Bottom 50 or random)
    sample_df = df.sample(min(100, len(df)))
    
    audit_results = []
    
    print(f"🔍 Auditing {len(sample_df)} records...")
    
    for idx, row in sample_df.iterrows():
        name = row['owner_name']
        old_race = 'Indian' # Since they are in the Hindu file
        
        # Predict again with NEW logic
        pred = predictor.predict_race(name)
        
        audit_results.append({
            'owner_name': name,
            'new_predicted_race': pred['predicted_race'],
            'is_hindu': pred.get('is_hindu', False),
            'category': pred.get('sub_category', 'N/A'),
            'confidence': pred['confidence'],
            'method': pred['method']
        })

    audit_df = pd.DataFrame(audit_results)
    
    # 2. Stats
    hindu_count = audit_df[audit_df['is_hindu']].shape[0]
    other_count = audit_df[~audit_df['is_hindu']].shape[0]
    
    print(f"\n📈 Audit Summary:")
    print(f"✅ Confirmed Hindu: {hindu_count}")
    print(f"⚠️ Re-classified/Non-Hindu: {other_count}")
    
    if other_count > 0:
        print("\n🔍 Sample of Re-classified Names:")
        print(audit_df[~audit_df['is_hindu']][['owner_name', 'new_predicted_race', 'method']].head(10))

    # 3. Save audit report
    report_file = 'data/Accuracy_Audit_Report.xlsx'
    audit_df.to_excel(report_file, index=False)
    print(f"\n📂 Detailed audit report saved to {report_file}")

if __name__ == "__main__":
    run_accuracy_audit()
