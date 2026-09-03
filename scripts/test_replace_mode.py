"""Scratch test for requeue + replace semantics (cleans up after itself)."""
import sqlite3
import sys

sys.path.insert(0, ".")
from src.robust_database import PropertyDatabase

db = PropertyDatabase("data/property_search.db")
rd = sqlite3.connect("data/property_search.db", timeout=30)

rd.execute(
    "INSERT OR REPLACE INTO search_progress (street_name, county, status, completed_at) "
    "VALUES ('ZZZTEST ONE', 'MONTGOMERY COUNTY', 'completed', '2001-01-01')"
)
rd.execute(
    "INSERT OR REPLACE INTO search_progress (street_name, county, status) "
    "VALUES ('ZZZTEST TWO', 'HOWARD COUNTY', 'completed')"
)
rd.commit()

# plain append must NOT touch completed rows
db.add_streets_to_batch(
    [("ZZZTEST ONE", "MONTGOMERY COUNTY"), ("ZZZTEST TWO", "HOWARD COUNTY")],
    999, requeue_completed=False)
print("after plain append:",
      rd.execute("SELECT street_name, status FROM search_progress "
                 "WHERE street_name LIKE 'ZZZTEST%' ORDER BY street_name").fetchall())

# requeue (force/refresh) flips both to pending on the new batch
db.add_streets_to_batch(
    [("ZZZTEST ONE", "MONTGOMERY COUNTY"), ("ZZZTEST TWO", "HOWARD COUNTY")],
    998, requeue_completed=True)
print("after requeue:",
      rd.execute("SELECT street_name, status, batch_id FROM search_progress "
                 "WHERE street_name LIKE 'ZZZTEST%' ORDER BY street_name").fetchall())

print("delete no-op:", db.delete_street_results("ZZZ NO SUCH STREET", "MONTGOMERY COUNTY"))

props = [
    {"street_name": "ZZZTEST", "county": "MONTGOMERY COUNTY", "owner_name": "A",
     "address": "1 ZZZ ST", "source_street": "ZZZTEST"},
    {"street_name": "ZZZTEST", "county": "MONTGOMERY COUNTY", "owner_name": "B",
     "address": "2 ZZZ ST", "source_street": "ZZZTEST"},
]
db.add_properties(props, 999)
fresh = [{"street_name": "ZZZTEST", "county": "MONTGOMERY COUNTY", "owner_name": "C",
          "address": "3 ZZZ ST", "source_street": "ZZZTEST"}]
db.add_properties(fresh, 998, replace=True)
print("replace-mode rows (expect only C):",
      rd.execute("SELECT owner_name FROM properties "
                 "WHERE source_street = 'ZZZTEST'").fetchall())

rd.execute("DELETE FROM search_progress WHERE street_name LIKE 'ZZZTEST%'")
rd.execute("DELETE FROM properties WHERE source_street = 'ZZZTEST'")
rd.execute("DELETE FROM batches WHERE id IN (998, 999)")
rd.commit()
print("cleanup rows left:",
      rd.execute("SELECT COUNT(*) FROM properties WHERE source_street = 'ZZZTEST'").fetchone())
rd.close()
db.close()
