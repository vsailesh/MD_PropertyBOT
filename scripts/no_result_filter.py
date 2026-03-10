#!/usr/bin/env python3
"""
No Result Found Predictor - CLI Script
Train and use the ML model to filter streets that are likely to return "No Result Found".
"""

import os
import sys

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.no_result_predictor import (
    NoResultPredictor, NoResultFilter, StreetNameFeatureExtractor
)


def main():
    """CLI interface for training and filtering."""
    import argparse

    parser = argparse.ArgumentParser(
        description='No Result Found Predictor - ML-based street filtering',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Train model from existing database
  python scripts/no_result_filter.py train

  # Predict single street
  python scripts/no_result_filter.py predict "MAIN ST" "Montgomery"

  # Filter Excel file before searching
  python scripts/no_result_filter.py filter data/streets.xlsx -o data/filtered.xlsx

  # Analyze rejected streets
  python scripts/no_result_filter.py analyze data/rejected_streets.xlsx
        """
    )

    subparsers = parser.add_subparsers(dest='command', help='Available commands')

    # Train command
    train_parser = subparsers.add_parser('train', help='Train model from database')
    train_parser.add_argument('--db', default='data/property_search.db',
                             help='Path to SQLite database')
    train_parser.add_argument('--output', default='models/no_result_predictor.joblib',
                             help='Output model path')

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
                              help='Output Excel file for filtered streets')
    filter_parser.add_argument('--rejected', default='data/rejected_streets.xlsx',
                              help='Rejected streets output')
    filter_parser.add_argument('--model', default='models/no_result_predictor.joblib',
                              help='Path to model file')
    filter_parser.add_argument('--threshold', type=float, default=0.6,
                              help='Filter threshold (0-1)')

    # Analyze command
    analyze_parser = subparsers.add_parser('analyze', help='Analyze rejected streets')
    analyze_parser.add_argument('input', help='Rejected streets Excel file')

    # Features command
    feats_parser = subparsers.add_parser('features', help='Show features for a street')
    feats_parser.add_argument('street', help='Street name')
    feats_parser.add_argument('county', help='County')

    args = parser.parse_args()

    if args.command == 'train':
        print("🔄 Training No Result Predictor from database...")
        print(f"   Database: {args.db}")

        predictor = NoResultPredictor()
        stats = predictor.train_from_database(args.db)

        if stats.get('success'):
            # Ensure model directory exists
            os.makedirs(os.path.dirname(args.output) or '.', exist_ok=True)
            predictor.save_model(args.output)

            print(f"\n✅ Model trained and saved to {args.output}")
            print(f"   Training samples: {stats['samples']}")
            if 'auc' in stats:
                print(f"   AUC Score: {stats['auc']:.4f}")
                print(f"   Precision: {stats['precision']:.4f}")
                print(f"   Recall: {stats['recall']:.4f}")
        else:
            print(f"\n❌ Training failed: {stats.get('reason')}")
            print(f"   Samples: {stats.get('samples', 0)}")

    elif args.command == 'predict':
        predictor = NoResultPredictor(args.model)

        should_filter, prob, reason = predictor.predict(
            args.street, args.county, args.threshold
        )

        print(f"\n=== Prediction for '{args.street}' in '{args.county}' ===")
        print(f"Should filter: {'🚫 YES' if should_filter else '✅ NO'}")
        print(f"Failure probability: {prob:.2%}")
        print(f"Reason: {reason}")

    elif args.command == 'filter':
        print(f"🔍 Filtering streets from {args.input}...")
        print(f"   Model: {args.model}")
        print(f"   Threshold: {args.threshold}")

        predictor = NoResultPredictor(args.model)

        # Load streets from Excel
        import pandas as pd
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

        print(f"\n📖 Loaded {len(streets)} streets from {args.input}")

        # Filter
        passed, rejected = predictor.filter_batch(streets, args.threshold)

        print(f"\n=== Filter Results ===")
        print(f"Original:      {len(streets)}")
        print(f"Passed:        {len(passed)}")
        print(f"Rejected:      {len(rejected)}")
        print(f"Filtered out:  {len(rejected)/len(streets)*100:.1f}%")

        # Show some rejection reasons
        if rejected:
            print(f"\n=== Sample Rejections ===")
            for r in rejected[:10]:
                print(f"  {r['street_name'][:30]:30} | {r['probability']:.2f} | {r['reason'][:50]}")
            if len(rejected) > 10:
                print(f"  ... and {len(rejected) - 10} more")

        # Export results
        if passed:
            passed_df = pd.DataFrame(passed, columns=['street_name', 'county'])
            passed_df.to_excel(args.output, index=False)
            print(f"\n✅ Exported {len(passed)} passed streets to {args.output}")

        if rejected:
            filter_exporter = NoResultFilter()
            filter_exporter.export_rejected_streets(rejected, args.rejected)

    elif args.command == 'analyze':
        import pandas as pd

        print(f"📊 Analyzing rejected streets from {args.input}...")

        df = pd.read_excel(args.input)

        if df.empty:
            print("❌ No rejected streets found")
            return

        print(f"\n=== Rejection Analysis ===")
        print(f"Total rejected: {len(df)}")

        if 'probability' in df.columns:
            print(f"\n=== Confidence Distribution ===")
            high_conf = df[df['probability'] >= 0.9]
            med_conf = df[(df['probability'] >= 0.7) & (df['probability'] < 0.9)]
            low_conf = df[df['probability'] < 0.7]

            print(f"High confidence (≥90%):   {len(high_conf):>6} ({len(high_conf)/len(df)*100:>5.1f}%)")
            print(f"Medium confidence (70-90%): {len(med_conf):>6} ({len(med_conf)/len(df)*100:>5.1f}%)")
            print(f"Low confidence (<70%):     {len(low_conf):>6} ({len(low_conf)/len(df)*100:>5.1f}%)")

        if 'county' in df.columns:
            print(f"\n=== By County ===")
            county_counts = df['county'].value_counts()
            for county, count in county_counts.head(10).items():
                print(f"  {county:30} {count:>6}")

        if 'reason' in df.columns:
            print(f"\n=== Rejection Reasons ===")
            reason_counts = df['reason'].str.split('|').str[0].str.strip().value_counts()
            for reason, count in reason_counts.head(10).items():
                print(f"  {reason:40} {count:>6}")

    elif args.command == 'features':
        print(f"\n=== Features for '{args.street}' in '{args.county}' ===")
        features = StreetNameFeatureExtractor.extract_features(args.street, args.county)

        for feat, val in sorted(features.items()):
            print(f"  {feat:40} {val}")

    else:
        parser.print_help()


if __name__ == "__main__":
    main()
