import sys, os
sys.path.insert(0, os.path.abspath("."))

from services.tier_service import calculate_tier_position_sizing, build_tier_excel

res = calculate_tier_position_sizing(
    benchmark='MU',
    benchmark_shares=5,
    lookback_days=20,
    tickers=['NVDA', 'AAPL', 'AMD', 'TSLA', 'PLTR', 'SOUN']
)
print('Status:', res['status'])
print('Benchmark:', res['benchmark'])
print('Item count:', len(res['items']))
for it in res['items']:
    print(f"  {it['ticker']}: Price=${it['price']}, GK={it['gk_daily_pct']}%, Tier1={it['tier1_shares']} shares, Notional=${it['notional']}, Risk=${it['actual_risk']}, Parity={it['parity_pct']}%")

excel_buf = build_tier_excel(res)
print('Excel bytes:', len(excel_buf.getvalue()))
