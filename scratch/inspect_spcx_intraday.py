import sys, os
sys.path.insert(0, os.path.abspath("."))
from database import get_postgres_connection

with get_postgres_connection() as conn:
    with conn.cursor() as cur:
        # Check table intraday_chart for SPCX
        cur.execute("SELECT id FROM ticker WHERE UPPER(ticker) = 'SPCX'")
        spcx_id = cur.fetchone()[0]
        cur.execute("""
            SELECT COUNT(*), MIN(date), MAX(date) 
            FROM intraday_chart 
            WHERE id_ticker = %s
        """, (spcx_id,))
        print("intraday_chart count and range for SPCX:", cur.fetchone())

        cur.execute("""
            SELECT date, COUNT(*) 
            FROM intraday_chart 
            WHERE id_ticker = %s AND date >= '2026-08-03' AND date <= '2026-08-06'
            GROUP BY date ORDER BY date
        """, (spcx_id,))
        print("Bars per date around Aug 4-5:", cur.fetchall())
