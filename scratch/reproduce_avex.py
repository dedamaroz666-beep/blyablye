import sys, os
sys.path.insert(0, os.path.abspath("."))
import pandas as pd
import numpy as np
from database import get_stock_data, get_ticker_reports
from calculations import prepare_multi_asset_returns, calculate_groups_analysis, get_ticker_earnings_reaction_dates

df = get_stock_data('AVEX', 'HII', 'TXT')
print("Reports for AVEX:")
reps_avex = get_ticker_reports('AVEX')
print(reps_avex)

print("\nReports for HII:")
reps_hii = get_ticker_reports('HII')
print(reps_hii.head(5))

print("\nReports for TXT:")
reps_txt = get_ticker_reports('TXT')
print(reps_txt.head(5))

returns = prepare_multi_asset_returns(df, ['AVEX', 'HII', 'TXT'])
print("\nReturns shape:", returns.shape)

for p in ['earnings_8q', 'earnings_all', '1y']:
    try:
        res = calculate_groups_analysis(
            df,
            groups={"Group 1": ["AVEX", "HII", "TXT"]},
            benchmark="AVEX",
            period=p
        )
        print(f"\n--- Period: {p} ---")
        corr = res.get("correlation", {})
        for r_k, r_v in corr.items():
            print(f"  {r_k}: {r_v}")
    except Exception as e:
        print(f"Error for {p}: {e}")
