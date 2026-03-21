#!/usr/bin/env python3
import os
import sys

# Add src to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from src.no_result_predictor import NoResultPredictor

def test_model():
    model_path = "models/no_result_predictor.joblib"
    predictor = NoResultPredictor(model_path)
    
    test_cases = [
        # [Street Name, County, Description]
        ["MAIN", "Montgomery", "Valid Common Name"],
        ["OAKLAND", "Baltimore", "Valid Name"],
        ["ST MARYS", "St Mary's", "Valid - with County mapping"],
        ["MC NAMARA", "Allegany", "Valid - with Prefix"],
        ["10TH 1", "Montgomery", "Unit Number Stripping"],
        ["2ND STE 1700", "Montgomery", "Unit Indicator Stripping"],
        ["84TH AV 6010 TO 6002", "Prince George's", "Complex Unit Stripping"],
        ["MD 355", "Montgomery", "Preserve Route Number"],
        ["12-34-56", "Montgomery", "Junk - Pure Numbers"],
        ["---", "Carroll", "Junk - Dashes"],
        ["A", "Washington", "Junk - Single Char"],
        ["PQ", "Frederick", "Junk - Non-vowel short"],
        ["RAMP TO I-95", "Howard", "Junk - Road Instruction"],
        ["FROM BALTO", "Baltimore", "Junk - Road Instruction"],
        ["!!!!!!!", "Anne Arundel", "Junk - Special Chars"],
        ["HOUSE", "Frederick", "Borderline - Common word"],
        ["X", "Howard", "Known failure from logs"],
        ["101-A", "Montgomery", "Edge case - Alphanumeric"]
    ]
    
    print("\n" + "="*80)
    print(f"{'STREET NAME':<25} | {'COUNTY':<15} | {'PROB':<6} | {'FILTER?':<8} | {'REASON'}")
    print("-" * 80)
    
    for street, county, desc in test_cases:
        should_filter, prob, reason = predictor.predict(street, county, threshold=0.6, update_stats=False)
        filter_str = "🛑 FILTER" if should_filter else "✅ PASS"
        print(f"{street:<25} | {county:<15} | {prob:.2f} | {filter_str:<8} | {reason}")
    print("="*80 + "\n")

if __name__ == "__main__":
    if not os.path.exists("models/no_result_predictor.joblib"):
        print("❌ Model not found. Please run training first.")
    else:
        test_model()
