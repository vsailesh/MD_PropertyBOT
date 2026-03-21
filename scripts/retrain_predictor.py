#!/usr/bin/env python3
import os
import sys
import sqlite3
import pandas as pd
import numpy as np
import joblib
from datetime import datetime
from sklearn.model_selection import train_test_split
from sklearn.metrics import classification_report, roc_auc_score, confusion_matrix
from sklearn.ensemble import RandomForestClassifier
from sklearn.preprocessing import StandardScaler

# Add src to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from src.no_result_predictor import NoResultPredictor, StreetNameFeatureExtractor

def retrain():
    db_path = "data/property_search.db"
    model_path = "models/no_result_predictor.joblib"
    
    print(f"🚀 Starting ML Retraining...")
    print(f"📖 Loading data from {db_path}...")
    
    if not os.path.exists(db_path):
        print(f"❌ Database not found at {db_path}")
        return

    conn = sqlite3.connect(db_path)
    # Load all historical records that have a clear success/fail outcome
    query = """
    SELECT street_name, county, properties_found, status
    FROM search_progress
    WHERE status IN ('completed', 'failed')
    """
    df = pd.read_sql_query(query, conn)
    conn.close()
    
    print(f"📊 Total historical records: {len(df)}")
    
    if len(df) < 1000:
        print(f"⚠️ Warning: Low sample size ({len(df)}). Model may not be robust.")

    # Create target: 1 if "No Result" or "Failed", 0 if "Found Properties"
    # We define success as finding at least 1 property
    df['target'] = ((df['properties_found'] == 0) | (df['status'] == 'failed')).astype(int)
    
    print(f"📉 Class Distribution:")
    print(df['target'].value_counts(normalize=True))
    
    print("🧬 Extracting enhanced features...")
    features_list = []
    for i, row in df.iterrows():
        if i % 10000 == 0:
            print(f"  Processed {i}/{len(df)}...")
        feats = StreetNameFeatureExtractor.extract_features(row['street_name'], row['county'])
        features_list.append(feats)
    
    X = pd.DataFrame(features_list)
    y = df['target'].values
    
    feature_names = list(X.columns)
    
    # Train/Test Split
    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=0.2, random_state=42, stratify=y
    )
    
    print(f"🧠 Training Random Forest (n=200, depth=15)...")
    scaler = StandardScaler()
    X_train_scaled = scaler.fit_transform(X_train)
    X_test_scaled = scaler.transform(X_test)
    
    model = RandomForestClassifier(
        n_estimators=200,
        max_depth=15,
        min_samples_split=10,
        random_state=42,
        class_weight='balanced',
        n_jobs=-1
    )
    
    model.fit(X_train_scaled, y_train)
    
    # Evaluation
    y_pred = model.predict(X_test_scaled)
    y_prob = model.predict_proba(X_test_scaled)[:, 1]
    
    auc = roc_auc_score(y_test, y_prob)
    report = classification_report(y_test, y_pred)
    
    print("\n" + "="*40)
    print("🏆 MODEL PERFORMANCE")
    print("="*40)
    print(f"ROC-AUC Score: {auc:.4f}")
    print("\nClassification Report:")
    print(report)
    print("="*40)
    
    # Feature Importance
    importances = model.feature_importances_
    feat_imp = pd.DataFrame({
        'feature': feature_names,
        'importance': importances
    }).sort_values('importance', ascending=False)
    
    print("\n🌟 TOP 15 FEATURES:")
    print(feat_imp.head(15).to_string(index=False))
    
    # Save Model
    print(f"\n💾 Saving model to {model_path}...")
    os.makedirs(os.path.dirname(model_path), exist_ok=True)
    
    # We include rules from the database as well (streets that always fail)
    # Group by county to avoid cross-county exclusion
    failed_streets_by_county = {}
    for county in df['county'].unique():
        county_norm = str(county).upper()
        county_fails = df[(df['county'] == county) & (df['target'] == 1)]['street_name'].str.upper().unique()
        failed_streets_by_county[county_norm] = list(county_fails)
    
    model_data = {
        'model': model,
        'scaler': scaler,
        'feature_names': feature_names,
        'rule_based_failures': failed_streets_by_county,
        'metrics': {
            'auc': auc,
            'samples': len(df),
            'trained_at': datetime.now().isoformat()
        }
    }
    
    joblib.dump(model_data, model_path)
    print("✅ Retraining complete!")

if __name__ == "__main__":
    retrain()
