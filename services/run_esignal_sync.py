import sys
import os
import time
import datetime
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.timing_anomaly_engine import anomaly_engine, COLOR_MAP

def run_live_excel_sync(
    workbook_name: str = "analysis.xlsm",
    sheet_name: str = "Resulting_table",
    interval_sec: float = 1.0
):
    """
    Connects to running Excel instance, reads live prices from eSignal RTD,
    computes time-of-day baseline & sigma, and updates cell colors in Excel.
    """
    try:
        import win32com.client
    except ImportError:
        print("Please install pywin32: pip install pywin32")
        return

    print("=" * 60)
    print("eSignal <-> Excel Real-Time Anomaly Scanner")
    print(f"Target: {workbook_name} | Sheet: {sheet_name}")
    print("=" * 60)

    try:
        excel = win32com.client.Dispatch("Excel.Application")
    except Exception as e:
        print(f"[-] Could not connect to Excel: {e}")
        print("Make sure Excel is open with analysis.xlsm")
        return

    wb = None
    try:
        for w in excel.Workbooks:
            if workbook_name.lower() in w.Name.lower():
                wb = w
                break
        if not wb:
            wb = excel.ActiveWorkbook
    except Exception as e:
        print(f"[-] Error finding workbook: {e}")
        return

    if not wb:
        print("[-] No open workbook found in Excel.")
        return

    try:
        ws = wb.Sheets(sheet_name)
    except Exception:
        ws = wb.ActiveSheet

    print(f"[+] Connected to sheet: {ws.Name}")
    print("[+] Starting real-time anomaly scanning loop (Ctrl+C to stop)...")

    # Group columns: B(2), F(6), J(10), N(14), R(18), V(22), Z(26), AD(30), AH(34)
    col_bases = [2, 6, 10, 14, 18, 22, 26, 30, 34]

    # Map color categories to Excel RGB integers (0xBBGGRR for Excel Color property)
    # Orange: #E67E22 -> B: 0x22, G: 0x7E, R: 0xE6 -> RGB(230, 126, 34)
    # Green: #27AE60 -> RGB(39, 174, 96)
    # Yellow: #FCF3CF -> RGB(252, 243, 207)
    # Translucent Light Green: #D4EFDF -> RGB(212, 239, 223)
    def rgb_int(r, g, b):
        return r + (g << 8) + (b << 16)

    EXCEL_COLORS = {
        "anomaly_orange": rgb_int(230, 126, 34),       # > 1.0 sigma
        "anomaly_green": rgb_int(39, 174, 96),         # ~1.0 sigma
        "anomaly_yellow": rgb_int(252, 243, 207),      # 0.5 - 0.8 sigma
        "anomaly_light_green": rgb_int(212, 239, 223), # 0.3 - 0.5 sigma (translucent look)
        "none": rgb_int(255, 255, 255)
    }

    try:
        while True:
            now_et = datetime.datetime.now()
            t_str = now_et.strftime("%H:%M")

            anomalies_found = []

            for col in col_bases:
                for row in range(2, 68):
                    ticker_cell = ws.Cells(row, col + 1)
                    ticker = ticker_cell.Value
                    if not ticker:
                        continue
                    ticker = str(ticker).strip().upper()
                    if ticker in ["ETFS", "MAIN_DATA", "", "SECTORS_BIDS"]:
                        continue

                    price_cell = ws.Cells(row, col + 2)
                    move_cell = ws.Cells(row, col)

                    try:
                        p = float(price_cell.Value or 0.0)
                        m = float(move_cell.Value or 0.0)
                    except (ValueError, TypeError):
                        continue

                    if p <= 0:
                        continue

                    # Evaluate anomaly
                    eval_res = anomaly_engine.classify_anomaly(
                        ticker=ticker,
                        current_price=p,
                        time_str=t_str
                    )

                    cat = eval_res["color_category"]
                    if cat != "none":
                        anomalies_found.append((ticker, m, eval_res["sigma"], eval_res["color_label"]))
                        # Highlight cell in Excel
                        color_val = EXCEL_COLORS.get(cat)
                        if color_val:
                            move_cell.Interior.Color = color_val
                            if cat in ["anomaly_orange", "anomaly_green"]:
                                move_cell.Font.Color = rgb_int(255, 255, 255)
                                move_cell.Font.Bold = True
                            elif cat == "anomaly_yellow":
                                move_cell.Font.Color = rgb_int(125, 102, 8)
                            elif cat == "anomaly_light_green":
                                move_cell.Font.Color = rgb_int(20, 90, 50)
                    else:
                        move_cell.Interior.ColorIndex = -4142 # xlNone
                        move_cell.Font.Color = rgb_int(0, 0, 0)

            if anomalies_found:
                top_3 = sorted(anomalies_found, key=lambda x: x[2], reverse=True)[:3]
                summary = ", ".join([f"{t}: {m}% ({s}σ)" for t, m, s, _ in top_3])
                print(f"[{t_str}] Top Anomalies: {summary}")

            time.sleep(interval_sec)

    except KeyboardInterrupt:
        print("\n[+] Monitoring stopped by user.")

if __name__ == "__main__":
    run_live_excel_sync()
