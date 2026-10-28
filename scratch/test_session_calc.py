import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import asyncio
from app_fastapi import api_datalens_data

async def main():
    res = await api_datalens_data(tickers="AAPL,NVDA,ITUB", period="1m")
    print("Status:", res.get("status"))
    dates = res.get("dates", [])
    data = res.get("data", {})
    print(f"Total dates returned: {len(dates)}")
    for d in dates[:3]:
        print(f"=== Date: {d} ===")
        for sym in ["AAPL", "NVDA", "ITUB"]:
            t = data.get(d, {}).get(sym, {})
            print(f"{sym}:")
            print(f"   intra: {t.get('session_intra')}% (open {t.get('session_intra_start')} -> close {t.get('session_intra_end')})")
            print(f"   pre:   {t.get('session_pre')}% (start {t.get('session_pre_start')} @ {t.get('session_pre_time')} -> end {t.get('session_pre_end')})")
            print(f"   post:  {t.get('session_post')}% (start {t.get('session_post_start')} -> end {t.get('session_post_end')} @ {t.get('session_post_time')})")

if __name__ == "__main__":
    asyncio.run(main())
