import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.community_pipeline import RaceEthnicityPredictor

def test_predictor():
    predictor = RaceEthnicityPredictor()
    
    test_names = [
        "PATEL AMIT",
        "REDDY SRINIVAS",
        "KHAN, SALMAN",
        "SMITH, JOHN",
        "RAMESH AMIT",
        "GUPTA DEEPAK",
        "SHARMA",
        "SINGH, HARPREET",
        "LTD HOLDINGS",
        "WANG, WEI",
        "CHETTY",
        "VAIKUNTA, RAMESH",
        "WASHINGTON, GEORGE",
        "UNKNOWN LLC",
        "RAM",
        "AARAV",
        "DESHMUKH, ANJALI"
    ]
    
    print(f"\n{'Name'.ljust(25)} | {'Predicted Race'.ljust(15)} | {'Hindu?'.ljust(6)} | {'SubCat'.ljust(15)} | {'Confidence'} | {'Method'}")
    print("-" * 110)
    for name in test_names:
        res = predictor.predict_race(name)
        h = str(res.get('is_hindu', 'N/A'))
        print(f"{name.ljust(25)} | {res['predicted_race'].ljust(15)} | {h.ljust(6)} | {str(res.get('sub_category', '')).ljust(15)} | {res['confidence']:<10.1f} | {res['method']}")

if __name__ == '__main__':
    test_predictor()
