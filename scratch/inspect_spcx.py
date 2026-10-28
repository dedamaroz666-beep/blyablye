import sys, os
sys.path.insert(0, os.path.abspath("."))
from database import get_postgres_connection, get_ticker_reports

with get_postgres_connection() as conn:
    with conn.cursor() as cur:
        cur.execute("SELECT id, ticker FROM ticker WHERE UPPER(ticker) LIKE 'SPC%'")
        tickers = cur.fetchall()
        print("Tickers matching SPC*:", tickers)

        for tid, tname in tickers:
            cur.execute("SELECT COUNT(*) FROM daily WHERE id_ticker = %s", (tid,))
            cnt_daily = cur.fetchone()[0]
            cur.execute("SELECT COUNT(*) FROM intraday WHERE id_ticker = %s", (tid,))
            cnt_intra = cur.fetchone()[0]
            print(f"Ticker {tname} (id={tid}): {cnt_daily} daily rows, {cnt_intra} intraday rows")

        cur.execute("""
            SELECT table_name FROM information_schema.tables WHERE table_schema='public'
        """)
        tables = [t[0] for t in cur.fetchall()]
        print("Tables in public:", tables)
        
        # Check if there is an earnings or reports table
        for tbl in ["report", "earnings", "reports", "earnings_calendar"]:
            if tbl in tables:
                cur.execute(f"SELECT column_name FROM information_schema.columns WHERE table_name='{tbl}'")
                cols = [c[0] for c in cur.fetchall()]
                print(f"Columns in {tbl}:", cols)
                cur.execute(f"SELECT * FROM {tbl} LIMIT 5")
                print(f"Sample rows in {tbl}:", cur.fetchall())
