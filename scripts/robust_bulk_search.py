#!/usr/bin/env python3
"""
Robust Bulk Street Search with SQLite Backend

Features:
- Atomic writes - no data corruption on interruption
- Resume capability - continue from where you left off
- Progress tracking - real-time status updates
- Batch isolation - one failure doesn't ruin others
- Automatic backups - periodic checkpoint backups
- Data integrity - validation and checksums
"""

import os
import sys
import time
import signal
import threading
from datetime import datetime
from typing import List, Dict, Optional
import concurrent.futures

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd

from src.community_pipeline import SDATAutoScraper, RaceEthnicityPredictor, SDATFormatter
from src.robust_database import PropertyDatabase, SearchJobManager
from src.street_name_cleaner import StreetNameCleaner, export_duplicates_report

try:
    from src.no_result_predictor import NoResultFilter, NoResultPredictor
    PREDICTOR_AVAILABLE = True
except ImportError:
    PREDICTOR_AVAILABLE = False
    print("⚠️ No Result Predictor not available")


class RobustBulkSearch:
    """
    Robust bulk street search with crash recovery and resume capability.
    """

    def __init__(self, db_path: str = "data/property_search.db", use_filter: bool = True,
                 filter_threshold: float = 0.6, filter_model_path: str = None,
                 clean_streets: bool = True, remove_duplicates: bool = True):
        """
        Initialize the robust bulk searcher.

        Args:
            db_path: Path to SQLite database
            use_filter: Whether to use the No Result predictor filter
            filter_threshold: Probability threshold for filtering (0-1)
            filter_model_path: Path to trained filter model
            clean_streets: Whether to clean street names per SDAT rules
            remove_duplicates: Whether to remove duplicate streets
        """
        self.db = PropertyDatabase(db_path)
        self.job_manager = SearchJobManager(self.db)
        self.predictor = RaceEthnicityPredictor()
        self._shutdown_requested = False

        # Initialize Street Name Cleaner (SDAT-compliant)
        self.clean_streets = clean_streets
        self.remove_duplicates = remove_duplicates
        self.street_cleaner = StreetNameCleaner() if clean_streets else None

        # Initialize No Result filter
        self.use_filter = use_filter and PREDICTOR_AVAILABLE
        self.filter_threshold = filter_threshold
        self.no_result_filter = None
        self.filter_stats = {'filtered': 0, 'total_checked': 0}

        if self.use_filter:
            try:
                model_path = filter_model_path or "models/no_result_predictor.joblib"
                self.no_result_filter = NoResultFilter(
                    db_path=db_path,
                    model_path=model_path
                )
                # Try to load existing model or train new one
                if os.path.exists(model_path):
                    self.no_result_filter.train_or_load_model(force_retrain=False)
                    print(f"✅ No Result Filter enabled (threshold: {filter_threshold})")
                else:
                    # Try to train from existing database
                    if os.path.exists(db_path):
                        print("🔄 Training No Result Filter from existing data...")
                        if self.no_result_filter.train_or_load_model(force_retrain=True):
                            print(f"✅ No Result Filter trained and enabled (threshold: {filter_threshold})")
                        else:
                            print("⚠️ Could not train filter, continuing without it")
                            self.use_filter = False
                    else:
                        print("⚠️ No filter model found, continuing without filtering")
                        self.use_filter = False
            except Exception as e:
                print(f"⚠️ Could not initialize No Result Filter: {e}")
                self.use_filter = False

        # Setup signal handlers for graceful shutdown
        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)

    def _signal_handler(self, signum, frame):
        """Handle shutdown signals gracefully."""
        print("\n\n🛑 Shutdown signal received. Finishing current batch and saving...")
        self._shutdown_requested = True

    def _extract_street_name(self, addr: str) -> str:
        """Extract street name using SDATFormatter."""
        if not addr or pd.isna(addr):
            return ""
        formatted = SDATFormatter.format_address(str(addr))
        return formatted['street_name'].upper()

    def create_job_from_excel(self, excel_path: str, job_name: Optional[str] = None) -> int:
        """
        Create a search job from an Excel file.

        Args:
            excel_path: Path to Excel file with streets
            job_name: Optional name for the job

        Returns:
            Batch ID
        """
        if job_name is None:
            job_name = f"Search_{datetime.now().strftime('%Y%m%d_%H%M%S')}"

        print(f"📖 Loading streets from: {excel_path}")
        streets = self.job_manager.load_streets_from_excel(excel_path)

        if not streets:
            raise ValueError("No streets found in Excel file")

        print(f"✅ Found {len(streets)} streets to process")

        # Step 1: Clean street names according to SDAT rules
        if self.street_cleaner:
            print("\n🧹 Cleaning street names (SDAT standard)...")
            original_count = len(streets)

            # Convert to list of dicts for cleaner
            # streets can be tuples (street_name, county) or dicts
            streets_list = []
            for s in streets:
                if isinstance(s, tuple):
                    streets_list.append({'street_name': s[0], 'county': s[1]})
                elif isinstance(s, dict):
                    streets_list.append({'street_name': s.get('street_name', ''),
                                        'county': s.get('county', '')})
                else:
                    # Skip invalid entries
                    continue

            # Clean and deduplicate
            if self.remove_duplicates:
                streets_list, dup_stats = self.street_cleaner.remove_duplicates(
                    streets_list,
                    county_col='county',
                    street_col='street_name',
                    keep='first'
                )

                dup_count = dup_stats['removed']
                dup_groups = dup_stats['groups']

                print(f"   Removed: {dup_count} duplicate streets ({dup_groups} groups)")

                # Export duplicates report for review
                if dup_count > 0:
                    dup_report_path = f"data/duplicates_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
                    try:
                        pd.DataFrame(dup_stats['details']).to_excel(dup_report_path, index=False)
                        print(f"   📄 Duplicates report: {dup_report_path}")
                    except Exception as e:
                        print(f"   ⚠️ Could not save duplicates report: {e}")

            # Update streets with cleaned names and ensure they are tuples (street_name, county)
            streets = []
            for item in streets_list:
                cleaned_name = self.street_cleaner.clean_street_name(item['street_name'])
                if cleaned_name and cleaned_name != 'UNKNOWN':
                    streets.append((cleaned_name, item['county']))

            invalid_removed = original_count - len(streets)
            if invalid_removed > 0:
                print(f"   Removed: {invalid_removed} invalid street names")

            print(f"   ✅ After cleaning: {len(streets)} streets")

        # Step 2: Apply No Result filter if enabled
        if self.use_filter and self.no_result_filter and self.no_result_filter.predictor:
            print("\n🔍 Applying No Result Filter...")
            original_count = len(streets)
            streets, rejected = self.no_result_filter.predictor.filter_batch(
                streets, threshold=self.filter_threshold
            )

            filtered_count = original_count - len(streets)
            self.filter_stats['filtered'] += filtered_count
            self.filter_stats['total_checked'] += original_count

            print(f"   Filtered: {filtered_count}/{original_count} streets ({filtered_count/original_count*100:.1f}%)")
            print(f"   Remaining: {len(streets)} streets to search")

            # Export rejected streets for review
            if rejected:
                rejected_path = f"data/rejected_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
                self.no_result_filter.export_rejected_streets(rejected, rejected_path)

        batch_id = self.job_manager.create_search_job(streets, job_name)
        print(f"📋 Created job: {job_name} (Batch ID: {batch_id})")

        return batch_id

    def process_street(self, street_name: str, county: str,
                      batch_id: int, scraper: SDATAutoScraper) -> List[Dict]:
        """
        Process a single street search.

        Args:
            street_name: Street to search
            county: County for the street
            batch_id: Associated batch ID
            scraper: Thread-isolated SDATAutoScraper instance

        Returns:
            List of property records found
        """
        results = []

        try:
            # Clean street name
            clean_street = self._extract_street_name(street_name)

            if not clean_street or clean_street == 'UNKNOWN':
                return []

            print(f"  🔍 Searching: {clean_street} in {county}")

            # Search SDAT
            search_results = scraper.search_street_bulk(clean_street, county)

            if search_results:
                print(f"    ✅ Found {len(search_results)} properties")

                for prop in search_results:
                    # Predict race/ethnicity
                    prediction = self.predictor.predict_race(prop['owner_name'])

                    record = {
                        'street_name': clean_street,
                        'county': county,
                        'owner_name': prop['owner_name'],
                        'address': prop['address'],
                        'city': prop.get('city', ''),
                        'zip_code': prop.get('zip_code', ''),
                        'source_street': street_name,
                        'predicted_race': prediction.get('predicted_race'),
                        'race_confidence': prediction.get('confidence'),
                        'race_method': prediction.get('method'),
                        'is_hindu': prediction.get('is_hindu', False),
                        'sub_category': prediction.get('sub_category')
                    }
                    results.append(record)
            else:
                print(f"    ❌ No results found")

            return results

        except Exception as e:
            print(f"    ⚠️ Error processing {street_name}: {e}")
            raise

    def _worker_loop(self, worker_id: int, batch_id: int, shared_state: dict):
        """Worker thread loop to process pending streets."""
        # Each thread gets its own session-based scraper
        scraper = SDATAutoScraper(headless=True)
        
        try:
            while not self._shutdown_requested:
                task = self.db.get_next_pending_street(batch_id)
                if not task:
                    break

                street_name = task['street_name']
                county = task['county']

                try:
                    properties = self.process_street(street_name, county, batch_id, scraper)

                    if properties:
                        added = self.db.add_properties(properties, batch_id)
                        with shared_state['lock']:
                            shared_state['total_properties'] += added

                    self.db.mark_street_completed(
                        street_name, county,
                        properties_found=len(properties)
                    )

                    with shared_state['lock']:
                        shared_state['streets_processed'] += 1
                        streets_processed = shared_state['streets_processed']

                    # Periodic update logging and checkpoints
                    if streets_processed % shared_state['save_interval'] == 0:
                        progress = self.db.get_search_progress(batch_id)
                        print(f"\n📊 Progress: {progress['completed']}/{progress['total']} streets | "
                              f"{progress['total_properties']} properties | "
                              f"{progress['failed']} failed")
                        print(f"💾 Checkpoint: {streets_processed} streets processed")
                        
                        self.db.update_batch_progress(
                            batch_id,
                            streets_completed=progress['completed'],
                            properties_found=progress['total_properties']
                        )

                    # Periodic backup
                    if streets_processed % shared_state['backup_interval'] == 0:
                        backup_path = self.db.backup_database()
                        print(f"📦 Backup created: {backup_path}")

                except Exception as e:
                    print(f"    ❌ Failed on thread {worker_id}: {e}")
                    self.db.mark_street_completed(
                        street_name, county,
                        properties_found=0,
                        error=str(e)
                    )

                    continue

        finally:
            # Scraper cleanup (session handled by GC)
            pass


    def run_batch(self, batch_id: int, save_interval: int = 10,
                 backup_interval: int = 100, max_workers: int = 1):
        """
        Run a batch job with multiple concurrent workers.

        Args:
            batch_id: Batch ID to process
            save_interval: Save progress after N streets
            backup_interval: Create backup after N streets
            max_workers: Number of concurrent Chrome processes
        """
        batch_info = self.db.get_batch_status(batch_id)

        if not batch_info:
            raise ValueError(f"Batch {batch_id} not found")

        print(f"\n{'='*70}")
        print(f"🚀 RUNNING BATCH: {batch_info['batch_name']}")
        print(f"📊 Batch ID: {batch_id}")
        print(f"⚙️  Workers: {max_workers}")
        print(f"{'='*70}\n")

        # Reset any stuck in-progress items from previous crashes
        self.db.reset_in_progress_streets(batch_id)

        # No longer pre-warming Chrome

        shared_state = {
            'lock': threading.Lock(),
            'total_properties': 0,
            'streets_processed': 0,
            'save_interval': save_interval,
            'backup_interval': backup_interval
        }

        # Multi-Threaded Execution Pool
        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = [
                executor.submit(self._worker_loop, i, batch_id, shared_state)
                for i in range(max_workers)
            ]
            for future in concurrent.futures.as_completed(futures):
                try:
                    future.result()
                except Exception as e:
                    print(f"⚠️ Worker thread encountered a fatal error: {e}")

        # Final update
        progress = self.db.get_search_progress(batch_id)

        self.db.update_batch_progress(
            batch_id,
            streets_completed=progress['completed'],
            properties_found=progress['total_properties'],
            status='completed' if not self._shutdown_requested else 'interrupted'
        )

        # Create final backup
        backup_path = self.db.backup_database()
        print(f"\n📦 Final backup: {backup_path}")

        # Final statistics
        print(f"\n{'='*70}")
        print(f"📊 BATCH COMPLETE")
        print(f"{'='*70}")
        print(f"Total Streets: {progress['total']}")
        print(f"Completed: {progress['completed']}")
        print(f"Failed: {progress['failed']}")
        print(f"Total Properties: {progress['total_properties']}")
        print(f"{'='*70}\n")

        return progress

    def resume_job(self, batch_id: Optional[int] = None,
                   excel_path: Optional[str] = None) -> int:
        """
        Resume an existing job or create a new one.

        Args:
            batch_id: Existing batch ID to resume
            excel_path: Path to Excel file for new job

        Returns:
            Batch ID
        """
        if batch_id:
            # Resume existing
            batch_info = self.db.get_batch_status(batch_id)

            if not batch_info:
                print(f"❌ Batch {batch_id} not found")
                return None

            print(f"📋 Resuming batch: {batch_info['batch_name']}")
            remaining = self.job_manager.resume_job(batch_id)

            if remaining == 0:
                print("✅ Batch already complete!")
                return batch_id

            print(f"📊 {remaining} streets remaining")

            return batch_id

        elif excel_path:
            # Create new job
            return self.create_job_from_excel(excel_path)

        else:
            # List available batches
            batches = self.db.get_all_batches()

            if not batches:
                print("No existing batches found. Provide excel_path to create one.")
                return None

            print("\n📋 Available Batches:")
            for b in batches[:10]:  # Show first 10
                status = b['status']
                print(f"  ID {b['id']}: {b['batch_name']} - {status} "
                      f"({b['completed_streets']}/{b['total_streets']} streets)")

            if len(batches) > 10:
                print(f"  ... and {len(batches) - 10} more")

            return None

    def export_results(self, output_path: str = "data/Final_Owner_Results.xlsx",
                      hindu_only: bool = False):
        """
        Export results to Excel.

        Args:
            output_path: Path for output file
            hindu_only: Export only Hindu owners
        """
        filters = {'is_hindu': True} if hindu_only else {}

        print(f"📤 Exporting to: {output_path}")
        self.db.export_to_excel(output_path, filters)
        print("✅ Export complete")


# ============ CLI INTERFACE ============

def main():
    """CLI interface for robust bulk search."""
    import argparse

    parser = argparse.ArgumentParser(
        description='Robust Bulk Street Search with SQLite Backend',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Create new job and run (with all optimizations enabled by default)
  python scripts/robust_bulk_search.py run -i data/MD_street_Names.xlsx

  # Run with 50 concurrent workers
  python scripts/robust_bulk_search.py run -i data/MD_street_Names.xlsx -w 50

  # Resume existing job
  python scripts/robust_bulk_search.py run --resume 1

  # Disable ML filter (run all streets)
  python scripts/robust_bulk_search.py run -i data/MD_street_Names.xlsx --no-filter

  # Disable duplicate removal (keep all entries)
  python scripts/robust_bulk_search.py run -i data/MD_street_Names.xlsx --no-dedup

  # Export all results
  python scripts/robust_bulk_search.py export -o data/Results.xlsx

  # Export only Hindu owners
  python scripts/robust_bulk_search.py export --hindu-only -o data/Hindu_Owners.xlsx

  # Show statistics
  python scripts/robust_bulk_search.py stats

  # List batches
  python scripts/robust_bulk_search.py list

Pipeline Order:
  1. Load streets from Excel
  2. Clean street names (SDAT standard) - use --no-clean to skip
  3. Remove duplicates - use --no-dedup to skip
  4. Apply ML filter for "No Result" predictions - use --no-filter to skip
  5. Run searches on remaining streets
        """
    )

    subparsers = parser.add_subparsers(dest='command', help='Available commands')

    # Run command
    run_parser = subparsers.add_parser('run', help='Run or resume a bulk search')
    run_parser.add_argument('-i', '--input', help='Excel file with streets to search')
    run_parser.add_argument('--resume', type=int, metavar='BATCH_ID',
                           help='Resume an existing batch')
    run_parser.add_argument('--name', help='Name for the job')
    run_parser.add_argument('-w', '--workers', type=int, default=1,
                           help='Number of concurrent scrapers to run (default: 1)')
    run_parser.add_argument('--save-interval', type=int, default=50,
                           help='Save progress every N streets (default: 50)')
    run_parser.add_argument('--backup-interval', type=int, default=1000,
                           help='Backup every N streets (default: 1000)')
    run_parser.add_argument('--no-filter', action='store_true',
                           help='Disable the No Result predictor filter')
    run_parser.add_argument('--filter-threshold', type=float, default=0.6,
                           help='Filter threshold for ML predictor (0-1, default: 0.6)')
    run_parser.add_argument('--train-filter', action='store_true',
                           help='Force training/retraining of the filter model')
    run_parser.add_argument('--no-clean', action='store_true',
                           help='Disable SDAT street name cleaning')
    run_parser.add_argument('--no-dedup', action='store_true',
                           help='Disable duplicate removal')

    # Export command
    export_parser = subparsers.add_parser('export', help='Export results to Excel')
    export_parser.add_argument('-o', '--output',
                              default='data/Final_Owner_Results.xlsx',
                              help='Output file path')
    export_parser.add_argument('--hindu-only', action='store_true',
                              help='Export only Hindu owners')

    # Stats command
    subparsers.add_parser('stats', help='Show database statistics')

    # List command
    subparsers.add_parser('list', help='List all batches')

    # Check command
    check_parser = subparsers.add_parser('check', help='Run integrity check')
    check_parser.add_argument('--fix', action='store_true',
                             help='Attempt to fix issues found')

    args = parser.parse_args()

    searcher = RobustBulkSearch(
        use_filter=not getattr(args, 'no_filter', False),
        filter_threshold=getattr(args, 'filter_threshold', 0.6),
        clean_streets=not getattr(args, 'no_clean', False),
        remove_duplicates=not getattr(args, 'no_dedup', False)
    )

    # Handle train-filter command
    if args.command == 'run' and getattr(args, 'train_filter', False):
        print("🔄 Training filter model from existing data...")
        if searcher.no_result_filter:
            searcher.no_result_filter.train_or_load_model(force_retrain=True)
            print("✅ Filter model trained successfully")

    if args.command == 'run':
        batch_id = None

        if args.resume:
            batch_id = searcher.resume_job(batch_id=args.resume)
        elif args.input:
            batch_id = searcher.create_job_from_excel(args.input, args.name)
        else:
            # Try to resume the latest incomplete batch
            batches = searcher.db.get_all_batches()
            incomplete = [b for b in batches if b['status'] == 'in_progress']

            if incomplete:
                batch_id = searcher.resume_job(batch_id=incomplete[0]['id'])
            else:
                print("No incomplete batch found. Use --input to create a new job.")
                return

        if batch_id:
            searcher.run_batch(
                batch_id,
                save_interval=args.save_interval,
                backup_interval=args.backup_interval,
                max_workers=args.workers
            )

    elif args.command == 'export':
        searcher.export_results(args.output, args.hindu_only)

    elif args.command == 'stats':
        stats = searcher.db.get_statistics()

        print("\n" + "="*70)
        print("DATABASE STATISTICS")
        print("="*70)
        print(f"Total Properties: {stats['total_properties']:,}")
        print(f"Unique Streets: {stats['unique_streets']:,}")
        print(f"Unique Counties: {stats['unique_counties']}")
        print(f"Hindu Owners: {stats['hindu_count']:,}")
        print(f"Indian Owners: {stats['indian_count']:,}")

        print("\n--- Race Breakdown ---")
        for race, count in list(stats['race_breakdown'].items())[:10]:
            print(f"  {race}: {count:,}")

        print("\n--- County Breakdown ---")
        for county, count in list(stats['county_breakdown'].items())[:10]:
            print(f"  {county}: {count:,}")

        print("\n" + "="*70)

    elif args.command == 'list':
        batches = searcher.db.get_all_batches()

        print("\n" + "="*100)
        print(f"{'ID':<6} {'Name':<30} {'Status':<15} {'Streets':<15} {'Properties':<12}")
        print("="*100)

        for b in batches:
            street_str = f"{b['completed_streets']}/{b['total_streets']}"
            print(f"{b['id']:<6} {b['batch_name'][:30]:<30} {b['status']:<15} "
                  f"{street_str:<15} {b['properties_found']:<12,}")

        print("="*100)

    elif args.command == 'check':
        results = searcher.db.run_integrity_check()

        print(f"\n{'='*70}")
        print(f"Integrity Check: {'PASSED ✓' if results['passed'] else 'FAILED ✗'}")
        print(f"{'='*70}")

        for check in results['checks']:
            status = '✓' if check['passed'] else '✗'
            print(f"{status} {check['type']}: {check['details']}")

        print(f"{'='*70}\n")


if __name__ == "__main__":
    main()
