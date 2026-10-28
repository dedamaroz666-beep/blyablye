import sys, os
sys.path.insert(0, os.path.abspath("."))
from database import get_postgres_connection

with get_postgres_connection() as conn:
    with conn.cursor() as cur:
        # Check ticker table for SPCX
        cur.execute("SELECT id, ticker FROM ticker WHERE UPPER(ticker) = 'SPCX'")
        row = cur.fetchone()
        print("SPCX in ticker table:", row)
        if row:
            spcx_id = row[0]
            cur.execute("SELECT COUNT(*) FROM daily WHERE id_ticker = %s", (spcx_id,))
            print("Daily bars count for SPCX:", cur.fetchone()[0])
            
            cur.execute("SELECT MIN(date), MAX(date) FROM daily WHERE id_ticker = %s", (spcx_id,))
            print("Daily date range:", cur.fetchone())
            
            # Check if there are daily bars around August 4
            cur.execute("SELECT date, open, high, low, close, volume FROM daily WHERE id_ticker = %s AND date >= '2026-08-01' AND date <= '2026-08-10' ORDER BY date", (spcx_id,))
            print("Daily bars Aug 1-10 (2026):", cur.fetchall())

            # Check 2025 as well, or 2024
            cur.execute("SELECT date, open, high, low, close, volume FROM daily WHERE id_ticker = %s AND date >= '2025-08-01' AND date <= '2025-08-10' ORDER BY date", (spcx_id,))
            print("Daily bars Aug 1-10 (2025):", cur.fetchall())

            cur.execute("SELECT date, open, high, low, close, volume FROM daily WHERE id_ticker = %s AND date >= '2024-08-01' AND date <= '2024-08-10' ORDER BY date", (spcx_id,))
            print("Daily bars Aug 1-10 (2024):", cur.fetchall())

            # Check reports for SPCX
            cur.execute("SELECT * FROM reports WHERE id_ticker = %s", (spcx_id,))
            print("Reports for SPCX id:", cur.fetchall())

        # Check all reports with date around Aug 4 in the whole database!
        print("\nChecking all reports on Aug 4 across any year:")
        cur.execute("""
            SELECT t.ticker, r.date, r.time, r.fiscal_period, r.fiscal_year 
            FROM reports r 
            JOIN ticker t ON t.id = r.id_ticker 
            WHERE EXTRACT(MONTH FROM r.date) = 8 AND EXTRACT(DAY FROM r.date) = 4
            LIMIT 20
        """)
        print("Sample reports on Aug 4:", cur.fetchall())

        # Check if there is another ticker for Space Exploration or SpaceX or similar
        cur.execute("SELECT id, ticker FROM ticker WHERE UPPER(ticker) LIKE '%SPACE%' OR UPPER(ticker) LIKE '%SPAC%'")
        print("\nTickers with SPACE/SPAC:", cur.fetchall())
