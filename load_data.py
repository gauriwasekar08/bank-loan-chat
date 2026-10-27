"""Load the Bank Loan CSV into a local SQLite database (loans.db)."""
import sqlite3
import sys

import pandas as pd

CSV_PATH = sys.argv[1] if len(sys.argv) > 1 else "financial_loan.csv"
DB_PATH = "loans.db"
TABLE = "loans"

df = pd.read_csv(CSV_PATH)

# SQLite has no real DATE type. Store dates as ISO text (YYYY-MM-DD)
# so that comparisons and date functions work.
for col in df.columns:
    if col.endswith("_date"):
        df[col] = pd.to_datetime(df[col], dayfirst=True, errors="coerce").dt.strftime("%Y-%m-%d")

with sqlite3.connect(DB_PATH) as con:
    df.to_sql(TABLE, con, if_exists="replace", index=False)

print(f"Loaded {len(df):,} rows into {DB_PATH}.{TABLE}")
print(df.dtypes)
