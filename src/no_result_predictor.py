#!/usr/bin/env python3
"""
No Result Found Predictor - ML Model
Predicts which street searches are likely to return "No Result Found" before searching.
"""

import os
import re
import json
import sqlite3
from datetime import datetime
from typing import Dict, List, Tuple, Optional
import numpy as np
import pandas as pd
from pathlib import Path
import warnings

# Suppress scikit-learn warnings about feature names when using NumPy arrays
warnings.filterwarnings("ignore", category=UserWarning, module="sklearn")

try:
    from sklearn.ensemble import RandomForestClassifier, GradientBoostingClassifier
    from sklearn.linear_model import LogisticRegression
    from sklearn.model_selection import train_test_split, cross_val_score
    from sklearn.metrics import classification_report, confusion_matrix, roc_auc_score
    from sklearn.preprocessing import StandardScaler
    import joblib
    SKLEARN_AVAILABLE = True
except ImportError:
    SKLEARN_AVAILABLE = False
    print("⚠️ scikit-learn not available. Using rule-based prediction only.")


class StreetNameFeatureExtractor:
    """
    Extract features from street names for ML prediction.
    These features capture patterns that lead to "No Result Found".
    """

    # Known failure patterns from analysis
    KNOWN_FAILURE_PREFIXES = ['-', '_', '\'', '#', '&', '  ']
    KNOWN_SHORT_FAILURES = {'AA', 'B', 'F', 'G', 'H', 'BB', 'DD', 'DJ', 'FF', 'J', 'L', 'M', 'MY', 'NU', 'EW', 'TA', 'V', 'AR'}
    KNOWN_NUMBER_PATTERNS = {r'^\d+-\d+$', r'^\d+[A-Z]$', r'^\d+$'}
    KNOWN_INVALID_COUNTIES = {'Unknown County', 'Unknown', ''}

    # Street suffixes that should be removed (these are valid but may cause issues)
    STREET_SUFFIXES = {
        'AVE', 'AVENUE', 'ST', 'STREET', 'DR', 'DRIVE', 'RD', 'ROAD',
        'LN', 'LANE', 'CT', 'COURT', 'PL', 'PLACE', 'CIR', 'CIRCLE',
        'BLVD', 'BOULEVARD', 'WAY', 'TRL', 'TRAIL', 'PKWY', 'PARKWAY',
        'HWY', 'HIGHWAY', 'PK', 'PIKE', 'ROW', 'RUN', 'XING', 'CROSSING',
        'ALY', 'ALLEY', 'SQ', 'SQUARE', 'TER', 'TERRACE', 'LOOP'
    }

    # Direction indicators
    DIRECTIONS = {'N', 'S', 'E', 'W', 'NE', 'NW', 'SE', 'SW',
                  'NORTH', 'SOUTH', 'EAST', 'WEST', 'NORTHEAST',
                  'NORTHWEST', 'SOUTHEAST', 'SOUTHWEST'}

    @staticmethod
    def extract_features(street: str, county: str) -> Dict[str, float]:
        """Extract features from street name and county."""
        # NEW: Clean the street name first to normalize unit numbers etc.
        from src.street_name_cleaner import StreetNameCleaner
        cleaner = StreetNameCleaner()
        street = cleaner.clean_street_name(street)
        
        if not street:
            street = ''
        if not county:
            county = ''

        # Normalize
        street = street.upper().strip()
        county_norm = county.upper().strip()

        features = {}

        # Basic string features
        features['street_length'] = len(street)
        features['street_word_count'] = len(street.split())
        features['county_length'] = len(county_norm)

        # Linguistic features (detect gibberish)
        vowels = len(re.findall(r'[AEIOU]', street))
        consonants = len(re.findall(r'[BCDFGHJKLMNPQRSTVWXYZ]', street))
        features['vowel_count'] = vowels
        features['consonant_count'] = consonants
        features['vowel_ratio'] = vowels / len(street) if len(street) > 0 else 0
        features['consonant_ratio'] = consonants / len(street) if len(street) > 0 else 0
        features['is_all_consonants'] = float(vowels == 0 and consonants > 0)

        # Alphanumeric mix detection
        features['has_letters'] = float(bool(re.search(r'[A-Z]', street)))
        features['has_numbers'] = float(bool(re.search(r'\d', street)))
        features['is_alphanumeric_mix'] = float(features['has_letters'] and features['has_numbers'])

        # Ramp/Instruction detection
        ramp_keywords = {'RAMP', 'TO', 'FROM', 'FR', 'VIA', 'US', 'MD', 'RT', 'ROUTE'}
        words = street.split()
        features['has_ramp_keywords'] = float(any(w in ramp_keywords for w in words))
        features['ramp_keyword_count'] = sum(1 for w in words if w in ramp_keywords)

        # Special character patterns
        features['starts_with_dash'] = float(street.startswith('-'))
        features['starts_with_underscore'] = float(street.startswith('_'))
        features['starts_with_apostrophe'] = float(street.startswith("'"))
        features['starts_with_special'] = float(any(street.startswith(c) for c in StreetNameFeatureExtractor.KNOWN_FAILURE_PREFIXES[:3]))

        # Has special characters anywhere
        features['has_special_chars'] = float(bool(re.search(r"[^A-Z0-9'\s\-]", street)))
        features['has_hyphen'] = float('-' in street)
        features['has_apostrophe'] = float("'" in street)
        features['has_parenthesis'] = float('(' in street or ')' in street)

        # Number patterns
        features['is_pure_number'] = float(street.isdigit() if street else False)
        features['starts_with_number'] = float(bool(re.match(r'^\d+', street)))
        features['is_number_range'] = float(bool(re.match(r'^\d+-\d+$', street)))
        features['is_number_letter'] = float(bool(re.match(r'^\d+[A-Z]$', street)))
        features['has_multiple_numbers'] = float(len(re.findall(r'\d+', street)) > 1)

        # Very short streets (known to fail often)
        features['is_very_short'] = float(len(street) < 4)
        features['is_single_char'] = float(len(street) == 1)
        features['is_double_char'] = float(len(street) == 2)
        features['is_triple_char'] = float(len(street) == 3)
        features['is_known_short_failure'] = float(street in StreetNameFeatureExtractor.KNOWN_SHORT_FAILURES)

        # Street suffixes and directions
        features['ends_with_suffix'] = float(any(words[-1].endswith(s) if words else False for s in StreetNameFeatureExtractor.STREET_SUFFIXES))
        features['has_direction'] = float(any(word in StreetNameFeatureExtractor.DIRECTIONS for word in words))
        features['first_word_direction'] = float(words[0] in StreetNameFeatureExtractor.DIRECTIONS if words else False)
        features['last_word_direction'] = float(words[-1] in StreetNameFeatureExtractor.DIRECTIONS if words else False)

        # Complex patterns
        features['has_multiple_spaces'] = float(street.count(' ') > 2)
        features['has_consecutive_spaces'] = float('  ' in street)
        features['has_space_dash_space'] = float(' - ' in street)

        # Ordinal patterns (like 33RD, 15TH) - these are valid
        features['is_ordinal'] = float(bool(re.match(r'^\d+(ST|ND|RD|TH)$', street)))

        # County features
        features['county_is_empty'] = float(len(county_norm) == 0)
        features['county_has_county'] = float('COUNTY' in county_norm)
        features['county_is_unknown'] = float(county_norm in StreetNameFeatureExtractor.KNOWN_INVALID_COUNTIES)

        # County-specific failure rates (from analysis)
        high_failure_counties = {'HOWARD', 'ANNE ARUNDEL', 'PRINCE GEORGE\'S'}
        features['county_high_failure'] = float(any(c in county_norm for c in high_failure_counties))

        # Combined features
        features['short_and_high_failure_county'] = float(features['is_very_short'] and features['county_high_failure'])

        return features


class NoResultPredictor:
    """
    ML-based predictor for "No Result Found" searches.
    Uses both ML model (if available) and rule-based prediction.
    """

    def __init__(self, model_path: Optional[str] = None):
        """
        Initialize the predictor.

        Args:
            model_path: Path to saved model file
        """
        self.model = None
        self.scaler = None
        self.feature_names = None
        self.rule_based_failures = {} # Dict of county -> set of failed streets
        self.stats = {
            'total_predictions': 0,
            'ml_filtered': 0,
            'rule_filtered': 0,
            'passed': 0
        }

        if model_path and os.path.exists(model_path):
            self.load_model(model_path)

    def load_model(self, model_path: str) -> bool:
        """Load a trained model from disk."""
        try:
            model_data = joblib.load(model_path)
            self.model = model_data.get('model')
            self.scaler = model_data.get('scaler')
            self.feature_names = model_data.get('feature_names')
            # Handle both old set format and new dict format for backward compatibility
            raw_failures = model_data.get('rule_based_failures', [])
            if isinstance(raw_failures, list):
                self.rule_based_failures = {'__GLOBAL__': set(raw_failures)}
            else:
                self.rule_based_failures = {k: set(v) for k, v in raw_failures.items()}
            print(f"✅ Loaded model from {model_path}")
            return True
        except Exception as e:
            print(f"⚠️ Could not load model: {e}")
            return False

    def save_model(self, model_path: str) -> bool:
        """Save the trained model to disk."""
        if not self.model:
            print("⚠️ No model to save")
            return False

        try:
            model_data = {
                'model': self.model,
                'scaler': self.scaler,
                'feature_names': self.feature_names,
                'rule_based_failures': {k: list(v) for k, v in self.rule_based_failures.items()},
                'trained_at': datetime.now().isoformat()
            }
            joblib.dump(model_data, model_path)
            print(f"✅ Saved model to {model_path}")
            return True
        except Exception as e:
            print(f"⚠️ Could not save model: {e}")
            return False

    def train_from_database(self, db_path: str = "data/property_search.db",
                          min_samples: int = 100) -> Dict:
        """
        Train the model from existing search results in the database.

        Args:
            db_path: Path to SQLite database
            min_samples: Minimum samples required for training

        Returns:
            Training statistics
        """
        conn = sqlite3.connect(db_path)

        # Get training data
        query = """
        SELECT street_name, county, properties_found, status
        FROM search_progress
        WHERE status IN ('completed', 'failed')
        ORDER BY random()
        """

        df = pd.read_sql_query(query, conn)
        conn.close()

        if len(df) < min_samples:
            print(f"⚠️ Not enough samples for training ({len(df)} < {min_samples})")
            return {'success': False, 'reason': 'insufficient_samples', 'samples': len(df)}

        # Create labels: 1 = likely to fail (no results), 0 = likely to succeed
        df['label'] = ((df['properties_found'] == 0) | (df['status'] == 'failed')).astype(int)

        # Extract features
        print("Extracting features...")
        features_list = []
        for _, row in df.iterrows():
            feats = StreetNameFeatureExtractor.extract_features(
                row['street_name'], row['county']
            )
            features_list.append(feats)

        X = pd.DataFrame(features_list)
        y = df['label'].values

        # Store feature names
        self.feature_names = list(X.columns)

        # Split data
        X_train, X_test, y_train, y_test = train_test_split(
            X, y, test_size=0.2, random_state=42, stratify=y
        )

        # Scale features
        self.scaler = StandardScaler()
        X_train_scaled = self.scaler.fit_transform(X_train)
        X_test_scaled = self.scaler.transform(X_test)

        # Train model
        if SKLEARN_AVAILABLE:
            print("Training Random Forest model...")
            self.model = RandomForestClassifier(
                n_estimators=100,
                max_depth=10,
                min_samples_split=5,
                min_samples_leaf=2,
                random_state=42,
                class_weight='balanced'
            )
            self.model.fit(X_train_scaled, y_train)

            # Evaluate
            y_pred = self.model.predict(X_test_scaled)
            y_prob = self.model.predict_proba(X_test_scaled)[:, 1]

            report = classification_report(y_test, y_pred, output_dict=True)
            auc = roc_auc_score(y_test, y_prob)

            # Feature importance
            importance = pd.DataFrame({
                'feature': self.feature_names,
                'importance': self.model.feature_importances_
            }).sort_values('importance', ascending=False)

            print("\n=== Feature Importance ===")
            print(importance.head(10).to_string())

            print(f"\n=== Model Performance ===")
            print(f"AUC: {auc:.4f}")
            print(f"Precision (fail prediction): {report['1']['precision']:.4f}")
            print(f"Recall (fail prediction): {report['1']['recall']:.4f}")

            # Also build rule-based failures from training data
            failed_streets = df[df['label'] == 1]['street_name'].str.upper().unique()
            self.rule_based_failures = set(failed_streets)

            return {
                'success': True,
                'samples': len(df),
                'auc': auc,
                'precision': report['1']['precision'],
                'recall': report['1']['recall'],
                'feature_importance': importance.head(10).to_dict()
            }
        else:
            print("⚠️ scikit-learn not available. Using rule-based only.")
            # Just build rule-based failures
            failed_streets = df[df['label'] == 1]['street_name'].str.upper().unique()
            self.rule_based_failures = set(failed_streets)
            return {'success': True, 'method': 'rule_based', 'samples': len(df)}

    def predict(self, street_name: str, county: str, threshold: float = 0.6, 
                update_stats: bool = True, ml_fallback: bool = True) -> Tuple[bool, float, str]:
        """
        Predict if a street search is likely to return "No Result Found".

        Args:
            street_name: Street name to check
            county: County for the street
            threshold: Probability threshold for filtering (0-1)
            update_stats: Whether to update internal statistics
            ml_fallback: Whether to use ML if rules don't match
        """
        if update_stats:
            self.stats['total_predictions'] += 1

        # Normalize inputs
        street = street_name.upper().strip() if street_name else ''
        county_norm = county.upper().strip() if county else ''

        # Rule-based checks (high confidence)
        reasons = []

        # Rule 1: Known failed streets (Check specific county or global)
        if county_norm in self.rule_based_failures:
            if street in self.rule_based_failures[county_norm]:
                if update_stats: self.stats['rule_filtered'] += 1
                return True, 1.0, f"Known failed street in {county_norm}: {street}"
        
        if '__GLOBAL__' in self.rule_based_failures:
             if street in self.rule_based_failures['__GLOBAL__']:
                if update_stats: self.stats['rule_filtered'] += 1
                return True, 1.0, f"Known global failed street: {street}"

        # Rule 2: Single/double character streets (high failure rate)
        if len(street) <= 2 and street not in {'ST', 'RD', 'DR', 'AVE', 'LN', 'CT', 'PL', 'RD'}:
            if update_stats: self.stats['rule_filtered'] += 1
            return True, 0.95, f"Very short street name: {street}"

        # Rule 3: Streets starting with special characters
        if street and street[0] in '-_\'#&!@$*':
            if update_stats: self.stats['rule_filtered'] += 1
            return True, 0.90, f"Starts with special character: {street[0]}"

        # Rule 4: Pure number patterns (like 02-0858-64)
        if re.match(r'^[\d-]+$', street):
            if update_stats: self.stats['rule_filtered'] += 1
            return True, 0.85, f"Pure number pattern: {street}"

        # Rule 5: Space-dash-space pattern
        if ' - ' in street:
            if update_stats: self.stats['rule_filtered'] += 1
            return True, 0.80, f"Contains space-dash-space pattern"

        # ML-based prediction (if available) - skip if ml_fallback is False
        if not ml_fallback:
            return False, 0.0, "Rules passed, skipping ML fallback"

        if self.model and self.scaler and self.feature_names:
            features = StreetNameFeatureExtractor.extract_features(street, county_norm)

            # Ensure all expected features are present
            feature_vector = [features.get(fname, 0.0) for fname in self.feature_names]

            # Use raw numpy for speed instead of DataFrame
            X_scaled = self.scaler.transform([feature_vector])
            prob = self.model.predict_proba(X_scaled)[0, 1]

            if prob >= threshold:
                if update_stats: self.stats['ml_filtered'] += 1
                return True, prob, f"ML prediction (prob={prob:.2f})"

        # Default: allow search
        if update_stats: self.stats['passed'] += 1
        return False, 0.0, "Passed all checks"

    def filter_batch(self, streets: List[Tuple[str, str]],
                    threshold: float = 0.6) -> Tuple[List[Tuple[str, str]], List[Dict]]:
        """
        Filter a batch of streets using vectorized inference (100x faster).
        """
        from joblib import Parallel, delayed
        
        # Phase 1: Rapid Rule-based pre-filter (fast, bypasses ML overhead)
        passed_rules = []
        rejected = []
        
        print(f"🧬 Pre-filtering {len(streets)} streets using rules...")
        for item in streets:
            # Handle both tuple and dict formats
            if isinstance(item, tuple):
                s, c = item
            else:
                s, c = item.get('street_name', ''), item.get('county', '')
                
            # Use rule-only check for pre-filtering
            res, prob, reason = self.predict(s, c, threshold, update_stats=False, ml_fallback=False)
            if res:
                # Filtered by rule
                rejected.append({
                    'street_name': s, 'county': c,
                    'probability': prob, 'reason': reason
                })
                self.stats['rule_filtered'] += 1
                self.stats['total_predictions'] += 1
            else:
                passed_rules.append((s, c))
                
        if not self.model or not passed_rules:
            # No model loaded, just return rule-validated items
            self.stats['passed'] += len(passed_rules)
            self.stats['total_predictions'] += len(passed_rules)
            return passed_rules, rejected

        # Phase 2: Parallel Feature Extraction for remaining streets
        print(f"🧬 Vectorizing features for {len(passed_rules)} streets...")
        
        def extract_wrapper(street_info):
            s, c = street_info
            return StreetNameFeatureExtractor.extract_features(s, c)
            
        features_list = Parallel(n_jobs=-1)(
            delayed(extract_wrapper)(s_c) for s_c in passed_rules
        )
        
        # Phase 3: Matrix-based Vectorized Inference (Extreme Speed)
        print(f"🧬 Parallel ML inference (Vectorized)...")
        # Convert list of dicts to correctly ordered numpy matrix
        X_matrix = []
        for feats in features_list:
            X_matrix.append([feats.get(fname, 0.0) for fname in self.feature_names])
            
        X_scaled = self.scaler.transform(X_matrix)
        probs = self.model.predict_proba(X_scaled)[:, 1]
        
        # Phase 4: Reconstruct results and update stats accurately
        passed = []
        for i, prob in enumerate(probs):
            s, c = passed_rules[i]
            self.stats['total_predictions'] += 1
            
            if prob >= threshold:
                rejected.append({
                    'street_name': s, 'county': c,
                    'probability': prob, 'reason': f"ML prediction (prob={prob:.2f})"
                })
                self.stats['ml_filtered'] += 1
            else:
                passed.append((s, c))
                self.stats['passed'] += 1
                
        return passed, rejected

    def get_stats(self) -> Dict:
        """Get prediction statistics."""
        return self.stats.copy()

    def reset_stats(self):
        """Reset prediction statistics."""
        self.stats = {
            'total_predictions': 0,
            'ml_filtered': 0,
            'rule_filtered': 0,
            'passed': 0
        }


class NoResultFilter:
    """
    Integration layer for using the No Result predictor in the search pipeline.
    """

    def __init__(self, db_path: str = "data/property_search.db",
                 model_path: str = "models/no_result_predictor.joblib"):
        self.db_path = db_path
        self.model_path = model_path
        self.predictor = NoResultPredictor(model_path)

    def train_or_load_model(self, force_retrain: bool = False) -> bool:
        """
        Train a new model or load existing one.

        Args:
            force_retrain: Force retraining even if model exists

        Returns:
            True if model is ready to use
        """
        # Try loading existing model
        if not force_retrain and os.path.exists(self.model_path):
            if self.predictor.load_model(self.model_path):
                return True

        # Train new model
        print("Training new model from database...")
        stats = self.predictor.train_from_database(self.db_path)

        if stats.get('success'):
            # Ensure model directory exists
            os.makedirs(os.path.dirname(self.model_path), exist_ok=True)
            self.predictor.save_model(self.model_path)
            return True

        return False

    def export_rejected_streets(self, rejected: List[Dict],
                               output_path: str = "data/rejected_streets.xlsx"):
        """Export rejected streets to Excel for review."""
        df = pd.DataFrame(rejected)
        if not df.empty:
            # Create output directory if needed
            os.makedirs(os.path.dirname(output_path), exist_ok=True)

            with pd.ExcelWriter(output_path, engine='openpyxl') as writer:
                df.to_excel(writer, index=False, sheet_name='Rejected')

            print(f"✅ Exported {len(rejected)} rejected streets to {output_path}")

            # Print summary
            print("\n=== Rejection Summary ===")
            print(f"Total rejected: {len(rejected)}")

            if 'probability' in df.columns:
                high_conf = df[df['probability'] >= 0.9]
                med_conf = df[(df['probability'] >= 0.7) & (df['probability'] < 0.9)]
                low_conf = df[df['probability'] < 0.7]

                print(f"  High confidence (≥90%): {len(high_conf)}")
                print(f"  Medium confidence (70-90%): {len(med_conf)}")
                print(f"  Low confidence (<70%): {len(low_conf)}")


# CLI Interface
def main():
    """CLI interface for training and using the predictor."""
    import argparse

    parser = argparse.ArgumentParser(
        description='No Result Found Predictor - Train and filter streets'
    )

    subparsers = parser.add_subparsers(dest='command', help='Available commands')

    # Train command
    train_parser = subparsers.add_parser('train', help='Train model from database')
    train_parser.add_argument('--db', default='data/property_search.db',
                             help='Path to SQLite database')
    train_parser.add_argument('--output', default='models/no_result_predictor.joblib',
                             help='Output model path')
    train_parser.add_argument('--force', action='store_true',
                             help='Force retraining even if model exists')

    # Predict command
    pred_parser = subparsers.add_parser('predict', help='Predict single street')
    pred_parser.add_argument('street', help='Street name to check')
    pred_parser.add_argument('county', help='County')
    pred_parser.add_argument('--model', default='models/no_result_predictor.joblib',
                            help='Path to model file')
    pred_parser.add_argument('--threshold', type=float, default=0.6,
                            help='Filter threshold (0-1)')

    # Filter command
    filter_parser = subparsers.add_parser('filter', help='Filter streets from Excel')
    filter_parser.add_argument('input', help='Input Excel file')
    filter_parser.add_argument('--output', default='data/filtered_streets.xlsx',
                              help='Output Excel file')
    filter_parser.add_argument('--rejected', default='data/rejected_streets.xlsx',
                              help='Rejected streets output')
    filter_parser.add_argument('--model', default='models/no_result_predictor.joblib',
                              help='Path to model file')
    filter_parser.add_argument('--threshold', type=float, default=0.6,
                              help='Filter threshold (0-1)')

    # Stats command
    stats_parser = subparsers.add_parser('stats', help='Show prediction stats')
    stats_parser.add_argument('--model', default='models/no_result_predictor.joblib',
                             help='Path to model file')

    args = parser.parse_args()

    if args.command == 'train':
        predictor = NoResultPredictor()
        stats = predictor.train_from_database(args.db)

        if stats.get('success'):
            os.makedirs(os.path.dirname(args.output), exist_ok=True)
            predictor.save_model(args.output)
            print(f"\n✅ Model trained and saved to {args.output}")
        else:
            print(f"\n❌ Training failed: {stats.get('reason')}")

    elif args.command == 'predict':
        predictor = NoResultPredictor(args.model)

        should_filter, prob, reason = predictor.predict(
            args.street, args.county, args.threshold
        )

        print(f"\n=== Prediction for '{args.street}' in '{args.county}' ===")
        print(f"Should filter: {should_filter}")
        print(f"Failure probability: {prob:.2f}")
        print(f"Reason: {reason}")

    elif args.command == 'filter':
        predictor = NoResultPredictor(args.model)

        # Load streets from Excel
        df = pd.read_excel(args.input)

        # Normalize columns
        df.columns = [c.lower().strip() for c in df.columns]

        addr_col = None
        for col in ['address', 'street', 'street_name']:
            if col in df.columns:
                addr_col = col
                break

        if not addr_col:
            print("❌ No address/street column found in Excel file")
            return

        county_col = 'county' if 'county' in df.columns else None

        streets = []
        for _, row in df.iterrows():
            street = str(row[addr_col]).strip()
            county = str(row[county_col]).strip() if county_col else "Unknown"

            if street and street.lower() not in ('nan', 'none', ''):
                streets.append((street, county))

        print(f"\nLoaded {len(streets)} streets from {args.input}")

        # Filter
        passed, rejected = predictor.filter_batch(streets, args.threshold)

        print(f"\n=== Filter Results ===")
        print(f"Original: {len(streets)}")
        print(f"Passed: {len(passed)}")
        print(f"Rejected: {len(rejected)}")
        print(f"Filtered out: {len(rejected)/len(streets)*100:.1f}%")

        # Export results
        if passed:
            passed_df = pd.DataFrame(passed, columns=['street_name', 'county'])
            passed_df.to_excel(args.output, index=False)
            print(f"\n✅ Exported {len(passed)} passed streets to {args.output}")

        if rejected:
            filter_exporter = NoResultFilter()
            filter_exporter.export_rejected_streets(rejected, args.rejected)

    elif args.command == 'stats':
        predictor = NoResultPredictor(args.model)
        stats = predictor.get_stats()

        print("\n=== Prediction Statistics ===")
        print(f"Total predictions: {stats['total_predictions']}")
        print(f"ML filtered: {stats['ml_filtered']}")
        print(f"Rule filtered: {stats['rule_filtered']}")
        print(f"Passed: {stats['passed']}")


if __name__ == "__main__":
    main()
