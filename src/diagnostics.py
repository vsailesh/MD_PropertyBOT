import os
import time
import json
from datetime import datetime
from typing import Dict, Any, Optional

class ScrapeDiagnostics:
    """
    ML Diagnostic 'Teacher' pipeline. 
    Captures the visual and DOM state of the browser when SDAT scraping fails, 
    and diagnoses whether the issue is a block, a changed selector, or just empty results.
    """

    def __init__(self, log_dir: str = "logs/failures", config_path: str = "config/sdat_selectors.json"):
        self.log_dir = log_dir
        self.config_path = config_path
        os.makedirs(self.log_dir, exist_ok=True)
        
    def capture_failure_state(self, driver, street_name: str, county: str, error_msg: str) -> str:
        """
        Takes a snapshot of the current browser state when a failure occurs.
        Returns the path to the directory containing the evidence.
        """
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        safe_street = "".join(c for c in street_name if c.isalnum() or c in (" ", "-")).strip().replace(" ", "_")
        failure_id = f"{timestamp}_{county}_{safe_street}"
        
        failure_dir = os.path.join(self.log_dir, failure_id)
        os.makedirs(failure_dir, exist_ok=True)
        
        print(f"📸 Capturing failure state for {street_name} to {failure_dir}")
        
        # 1. Capture Screenshot
        try:
            screenshot_path = os.path.join(failure_dir, "screenshot.png")
            driver.save_screenshot(screenshot_path)
        except Exception as e:
            print(f"  ⚠️ Could not take screenshot: {e}")
            
        # 2. Capture HTML Rules / Page Source
        try:
            html_path = os.path.join(failure_dir, "page_source.html")
            with open(html_path, "w", encoding="utf-8") as f:
                f.write(driver.page_source)
        except Exception as e:
            print(f"  ⚠️ Could not save HTML: {e}")

        # 3. Capture Metadata
        metadata = {
            "timestamp": timestamp,
            "street_name": street_name,
            "county": county,
            "error_msg": str(error_msg),
            "current_url": driver.current_url
        }
        
        with open(os.path.join(failure_dir, "metadata.json"), "w") as f:
            json.dump(metadata, f, indent=2)
            
        # Trigger the ML Teacher analysis
        self.analyze_failure(failure_dir)
            
        return failure_dir

    def analyze_failure(self, failure_dir: str) -> Dict[str, Any]:
        """
        The "Teacher" Model.
        Currently uses heuristic text parsing to diagnose the captured HTML.
        In a full ML implementation, this would send the screenshot and HTML to an LLM Vision API (like GPT-4o).
        """
        print("🧠 Teacher diagnosing the failure...")
        
        diagnosis = {
            "reason": "UNKNOWN",
            "action_required": "Manual Review",
            "confidence": 0.0
        }
        
        html_path = os.path.join(failure_dir, "page_source.html")
        if not os.path.exists(html_path):
            return diagnosis
            
        with open(html_path, "r", encoding="utf-8") as f:
            html_content = f.read().lower()
            
        # Diagnosis 1: Rate Limit / Captcha / Blocked
        if any(trigger in html_content for trigger in [
            "access denied", "captcha", "security check", "too many requests", "cloudflare"
        ]):
            diagnosis["reason"] = "IP_BLOCKED_OR_CAPTCHA"
            diagnosis["action_required"] = "Pause scraping or rotate proxy."
            diagnosis["confidence"] = 0.95
            print("  🚨 Diagnosis: Access Denied / Security Check triggered.")
            
        # Diagnosis 2: Server Down
        elif "service unavailable" in html_content or "503 " in html_content or "server error" in html_content:
            diagnosis["reason"] = "SDAT_SERVER_DOWN"
            diagnosis["action_required"] = "Wait 15-30 minutes and retry."
            diagnosis["confidence"] = 0.90
            print("  🚨 Diagnosis: State of Maryland servers are down.")

        # Diagnosis 3: Missing Selectors (Website structure changed)
        # Note: If no records are found, that's not a failure we capture; we just return empty.
        # So a timeout on finding an element usually implies a structure change.
        else:
            diagnosis["reason"] = "HTML_STRUCTURE_CHANGED"
            diagnosis["action_required"] = "Review HTML to update selectors in config/sdat_selectors.json."
            diagnosis["confidence"] = 0.70
            print("  🚨 Diagnosis: Website structure changed. Cannot find HTML IDs.")
            
        # Append diagnosis to metadata
        meta_path = os.path.join(failure_dir, "metadata.json")
        try:
            with open(meta_path, "r") as f:
                metadata = json.load(f)
            metadata["diagnosis"] = diagnosis
            with open(meta_path, "w") as f:
                json.dump(metadata, f, indent=2)
        except Exception:
            pass
            
        return diagnosis
