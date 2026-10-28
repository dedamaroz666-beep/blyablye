import sys, os, sqlite3
sys.path.insert(0, os.path.abspath("."))
import pandas as pd
from database import get_ticker_reports, CACHE_DB_PATH
from services.earnings_reaction_service import earnings_reaction_client

# Let's inspect what happens if we merge reports_extra into get_ticker_reports
def test_mock_get_ticker_reports(ticker: str):
    ticker = str(ticker).strip().upper()
    df = get_ticker_reports(ticker)
    
    # Read reports_extra from SQLite
    try:
        with sqlite3.connect(CACHE_DB_PATH) as conn:
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()
            cur.execute("SELECT * FROM reports_extra WHERE UPPER(ticker) = ?", (ticker,))
            extra_rows = [dict(r) for r in cur.fetchall()]
    except Exception as e:
        print("SQLite error:", e)
        extra_rows = []

    if extra_rows:
        existing_dates = set(df["date"].astype(str).tolist()) if not df.empty else set()
        new_records = []
        for er in extra_rows:
            d_str = er.get("date")
            if d_str and d_str not in existing_dates:
                t_str = er.get("time") or "AMC"
                session_time = "16:05:00" if "AMC" in str(t_str).upper() else "08:00:00"
                y = d_str.split("-")[0]
                m = int(d_str.split("-")[1])
                q = f"Q{(m - 1) // 3 + 1}"
                new_records.append({
                    "ticker": ticker,
                    "date": d_str,
                    "fiscal_period": q,
                    "fiscal_year": y,
                    "time": session_time,
                    "move_date": d_str,
                    "benzinga_id": f"extra_{ticker}_{d_str}"
                })
        if new_records:
            df_new = pd.DataFrame(new_records)
            df = pd.concat([df, df_new], ignore_index=True) if not df.empty else df_new
            df = df.sort_values("date", ascending=False).reset_index(drop=True)

    return df

merged_df = test_mock_get_ticker_reports("SPCX")
print("Merged reports for SPCX:")
print(merged_df)

# Now test earnings_reaction_service with monkeypatch
import services.earnings_reaction_service as ers
old_get = ers.get_ticker_reports
ers.get_ticker_reports = test_mock_get_ticker_reports
earnings_reaction_client._cache.clear()

reactions = earnings_reaction_client.get_earnings_reaction_matrix("SPCX")
print(f"\nReactions calculated for SPCX: {len(reactions)}")
for r in reactions:
    print("Report row:", r)

ers.get_ticker_reports = old_get
