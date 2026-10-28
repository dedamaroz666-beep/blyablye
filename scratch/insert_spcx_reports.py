import sys, os
sys.path.insert(0, os.path.abspath("."))
from database import get_postgres_connection

with get_postgres_connection() as conn:
    with conn.cursor() as cur:
        # Check SPCX ticker id
        cur.execute("SELECT id FROM ticker WHERE UPPER(ticker) = 'SPCX'")
        spcx_id = cur.fetchone()[0]
        print("SPCX id:", spcx_id)

        cur.execute("SELECT MAX(id) FROM reports")
        max_id = cur.fetchone()[0] or 38827733

        max_id += 1
        cur.execute("""
            INSERT INTO reports (id, id_ticker, date, fiscal_period, fiscal_year, time, benzinga_id)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
        """, (max_id, spcx_id, '2026-08-04', 'Q2', '2026', '16:05:00', 'spcx_2026_q2_benzinga'))
        print(f"Inserted report 2026-08-04 with id {max_id}")

        max_id += 1
        cur.execute("""
            INSERT INTO reports (id, id_ticker, date, fiscal_period, fiscal_year, time, benzinga_id)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
        """, (max_id, spcx_id, '2026-11-03', 'Q3', '2026', '16:05:00', 'spcx_2026_q3_benzinga'))
        print(f"Inserted report 2026-11-03 with id {max_id}")

        conn.commit()

        # Query back to verify
        cur.execute("SELECT id, id_ticker, date, time, move_date, fiscal_period, fiscal_year, benzinga_id FROM reports WHERE id_ticker = %s", (spcx_id,))
        print("Inserted reports for SPCX:", cur.fetchall())

print("Insertion completed successfully.")
