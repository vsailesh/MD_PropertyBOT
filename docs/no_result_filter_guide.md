# No Result Found Predictor - ML Model

## Overview

The No Result Found Predictor is a machine learning model that predicts which street searches are likely to return "No Result Found" before searching, saving significant time and resources.

## Key Findings from Analysis

After analyzing the database of 33,557 search results, we identified these patterns that lead to failures:

### 1. Very Short Street Names (< 4 chars)
- Examples: `AA`, `B`, `F`, `G`, `H`, `BB`, `DD`, `DJ`, `J`, `L`
- These have extremely high failure rates

### 2. Malformed Inputs
- Streets starting with special characters (`-`, `_`, `'`, `#`, `&`)
- Number patterns like `02-0858-64`
- Space-dash-space patterns (` - `)

### 3. County-Specific Issues
- Howard County has higher failure rates
- Anne Arundel also shows elevated failures
- Certain street-county combinations fail consistently

### 4. Technical Failures
- HTML structure changes causing "Street name input field missing" errors
- Timeout issues on certain street types

## Model Performance

The trained Random Forest model achieves:
- **AUC Score: 0.83** - Good discrimination ability
- **Precision: 0.77** - 77% of predicted failures are actual failures
- **Recall: 0.86** - Catches 86% of actual failures
- **Training Samples: 33,557** search results

### Top Predictive Features (by importance):
1. `county_length` (58%) - County string characteristics
2. `county_high_failure` (21%) - Known high-failure counties
3. `street_word_count` (8%) - Number of words in street name
4. `street_length` (6.5%) - Length of street name
5. `has_numbers` (3.4%) - Presence of numbers in street name

## Usage

### Train the Model

```bash
# Train from existing database
python scripts/no_result_filter.py train --db data/property_search.db

# Train with custom output path
python scripts/no_result_filter.py train --db data/property_search.db --output models/custom_model.joblib
```

### Predict Single Street

```bash
# Check if a street is likely to fail
python scripts/no_result_filter.py predict "MAIN ST" "Montgomery"

# With custom threshold
python scripts/no_result_filter.py predict "ELPIN" "Howard" --threshold 0.7
```

### Filter Excel File Before Searching

```bash
# Filter streets from Excel file
python scripts/no_result_filter.py filter data/MD_street_Names.xlsx \
    --output data/filtered_streets.xlsx \
    --rejected data/rejected_streets.xlsx

# With custom threshold (more conservative)
python scripts/no_result_filter.py filter data/MD_street_Names.xlsx \
    --threshold 0.8
```

### Use in Bulk Search

The filter is now integrated into `robust_bulk_search.py`:

```bash
# Run with default filter (threshold 0.6)
python scripts/robust_bulk_search.py run -i data/MD_street_Names.xlsx

# Disable filtering
python scripts/robust_bulk_search.py run -i data/MD_street_Names.xlsx --no-filter

# Use custom threshold
python scripts/robust_bulk_search.py run -i data/MD_street_Names.xlsx --filter-threshold 0.8

# Force retrain the model before running
python scripts/robust_bulk_search.py run -i data/MD_street_Names.xlsx --train-filter
```

## Rule-Based Filtering

The predictor uses a hybrid approach:

### Rule-Based (High Confidence)
1. **Known failed streets** - Streets that previously failed in the database
2. **Very short names** (≤2 chars) - `AA`, `B`, `F`, etc.
3. **Special character prefixes** - Streets starting with `-`, `_`, etc.
4. **Pure number patterns** - Like `02-0858-64`
5. **Space-dash-space patterns** - Like ` - `

### ML-Based (Probabilistic)
For streets that don't match rule-based patterns, the ML model:
1. Extracts 40+ features from street name and county
2. Calculates failure probability using Random Forest
3. Filters if probability exceeds threshold (default 0.6)

## Expected Savings

Based on analysis:
- **~40-50%** of searches in typical datasets can be filtered out
- High-confidence filters (>90% probability) are extremely accurate
- This saves significant time and reduces SDAT server load

## Model Retraining

The model should be retrained periodically as:
- New search results accumulate
- New failure patterns emerge
- SDAT website structure changes

```bash
# Retrain from latest data
python scripts/no_result_filter.py train --db data/property_search.db
```

## File Structure

```
src/no_result_predictor.py     # Core ML module
scripts/no_result_filter.py     # CLI interface
models/no_result_predictor.joblib  # Trained model
data/rejected_*.xlsx            # Exported rejected streets
```

## Feature Engineering

The model uses 40+ features including:
- Street length, word count
- Special character patterns
- Number patterns (pure, ranges, letters)
- County characteristics
- Combined features (e.g., short street + high-failure county)
- Street suffix detection
- Direction indicators
- Ordinal detection (33RD, 15TH, etc.)

## Troubleshooting

**Model not loading:**
- Check if `models/no_result_predictor.joblib` exists
- Retrain the model: `python scripts/no_result_filter.py train`

**Too many streets filtered:**
- Lower the threshold: `--filter-threshold 0.8`
- Check rejected streets file to see patterns

**Filter not working:**
- Check if scikit-learn is installed: `pip install scikit-learn`
- Check database has sufficient training data
