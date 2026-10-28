import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.timing_anomaly_engine import anomaly_engine

def test_user_scenarios():
    print("--- Test 1: MCD at 05:00 AM moving 0.5% (historic baseline at 05:00 is ~0.10%) ---")
    res1 = anomaly_engine.classify_anomaly(
        ticker="MCD",
        current_price=238.9,
        prev_close=237.7,  # +0.50%
        time_str="05:00",
        speed_5m_pct=0.08  # Fast gap in 5 mins
    )
    print("MCD Result:", res1)
    assert res1["color_category"] in ["anomaly_green", "anomaly_orange"], f"Unexpected category {res1['color_category']}"
    print(f"-> MCD successfully flagged as {res1['color_label']} (Sigma: {res1['sigma']}, Color: {res1['bg_hex']})")

    print("\n--- Test 2: MCD at 09:20 AM moving 0.5% (normal move at 09:20 is ~0.50%) ---")
    res2 = anomaly_engine.classify_anomaly(
        ticker="MCD",
        current_price=238.9,
        prev_close=237.7,  # +0.50%
        time_str="09:20",
        speed_5m_pct=0.01  # Slow move
    )
    print("MCD at 09:20 Result:", res2)
    print(f"-> At 09:20 AM same 0.5% move has Sigma {res2['sigma']} and category: {res2['color_label']}")

    print("\n--- Test 3: Multiple tickers from screenshot ---")
    test_tickers = [
        ("AKAM", 136.0, 119.8, "05:00", 1.5),  # 13.5% huge jump -> should be orange
        ("ARM", 318.8, 313.6, "05:00", 0.4),   # ~1.65% jump -> orange/green
        ("PL", 18.37, 18.20, "06:00", 0.1),    # ~0.96% -> green
        ("RKLB", 74.93, 74.64, "06:00", 0.05), # ~0.39% -> light green (60% transparent)
    ]
    for sym, curr, prev, t_str, spd in test_tickers:
        res = anomaly_engine.classify_anomaly(sym, curr, prev, t_str, spd)
        print(f"[{sym}] Move: {res['pct_move']}% | Sigma: {res['sigma']} | Cat: {res['color_label']} | Hex: {res['bg_hex']}")

if __name__ == "__main__":
    test_user_scenarios()
