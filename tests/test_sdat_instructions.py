import os
import sys
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.community_pipeline import SDATFormatter

def test_sdat_formatting():
    test_cases = [
        # User's specific examples
        ("15th PL NORTHWEST", "15TH"),
        ("9439425 GEORGIA", "GEORGIA"),
        ("HIGHLAND AVE NORTHWEST", "HIGHLAND"),
        ("13400-13408 KINGSVIEW VILLAGE", "KINGSVIEW VILLAGE"),
        ("ORCHARD WAY SOUTH", "ORCHARD"),
        
        # Number stripping
        ("10109-B SEATTLE SLEW", "SEATTLE SLEW"),
        ("10042-B AMERICAN PHAROAH", "AMERICAN PHAROAH"),
        
        # Directions from any position
        ("8TH ST NORTHWEST", "8TH"),
        ("3RD ST NORTHWEST", "3RD"),
        ("DURHAM RD WEST", "DURHAM"),
        ("ELPIN DR WEST", "ELPIN"),
        ("CRAIN HWY NORTH", "CRAIN"),
        ("MAIN AVE SOUTHWEST", "MAIN"),
        ("SOMERSET PL NORTHWEST", "SOMERSET"),
        ("CAPITOL ST NORTHWEST", "CAPITOL"),
        ("PEABODY ST NORTHWEST", "PEABODY"),
        ("FEDERALSBURG SOUTH", "FEDERALSBURG"),
        
        # Original SDAT instruction examples
        ("9800 N Maryland St", "MARYLAND"),
        ("St. Mary's Church Road", "MARYS CHURCH"),
        ("O'Donnell Street", "O'DONNELL"),
        ("Saint Marys", "ST MARYS"),
        ("25th Street", "25TH"),
        ("33rd Ave", "33RD"),
        ("9999 Winter Sun Rd", "WINTER SUN"),
    ]
    
    print(f"{'Original':<40} | {'Expected':<20} | {'Actual':<20} | {'Status'}")
    print("-" * 100)
    
    passed = 0
    failed = 0
    for original, expected in test_cases:
        actual = SDATFormatter.format_address(original)['street_name']
        status = "✅ PASS" if actual == expected else "❌ FAIL"
        if actual == expected:
            passed += 1
        else:
            failed += 1
        print(f"{original:<40} | {expected:<20} | {actual:<20} | {status}")
        
    print(f"\n{'='*50}")
    print(f"Results: {passed} passed, {failed} failed out of {len(test_cases)}")
    if failed == 0:
        print("🎉 All SDAT instruction tests passed!")
    else:
        print("⚠️ Some tests failed. Please review.")

if __name__ == "__main__":
    test_sdat_formatting()
