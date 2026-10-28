import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter
from typing import Dict, List, Any, Optional

from services.timing_anomaly_engine import anomaly_engine, COLOR_MAP

# 9 column groups exactly matching the screenshot layout
COL_OFFSETS = [
    (2, "B", "C", "D"),      # Tech / High-Vol G1
    (6, "F", "G", "H"),      # Industrials / Travel G2
    (10, "J", "K", "L"),     # Semi / Tech G3
    (14, "N", "O", "P"),     # Tech Mega / AI G4
    (18, "R", "S", "T"),     # Cloud / Software G5
    (22, "V", "W", "X"),     # Space / Crypto / Growth G6
    (26, "Z", "AA", "AB"),   # China / Indices / ETFs G7
    (30, "AD", "AE", "AF"),  # Miners / Metals G8
    (34, "AH", "AI", "AJ")   # Energy / Oil & Gas G9
]

# Tickers mapped from the user's screenshot
TOP_BLOCK_DATA = [
    # Group 1 (B..D)
    [("MCD", 238.9, 1.07), ("NOK", 10.67, 1.04), ("FSLR", 174.0, 0.76), ("AGNC", 9.7, 0.72),
     ("ALAB", 367.4, 0.68), ("INTC", 129.4, 0.48), ("CLX", 81.99, 0.39), ("O", 55.51, 0.35),
     ("UBER", 69.33, 0.32), ("AMCR", 42.3, 0.31), ("AZN", 165.6, 0.26), ("USAR", 15.64, 0.26),
     ("FTI", 70.6, 0.23), ("KMB", 97.25, 0.18)],
    # Group 2 (F..H)
    [("AKAM", 136.0, 13.49), ("NCLH", 14.35, 0.21), ("CCL", 22.0, 0.21), ("F", 12.65, 0.06),
     ("IBM", 228.1, 0.02), ("CARR", 55.07, -0.06), ("NKE", 36.12, -0.08), ("DAL", 83.18, -0.13),
     ("UPS", 92.18, -0.16), ("HD", 292.4, -0.19), ("AXP", 306.5, -0.19), ("GS", 926.6, -0.20),
     ("LULU", 101.7, -0.28), ("UNH", 375.0, -0.30)],
    # Group 3 (J..L)
    [("HPE", 64.64, 0.81), ("CIEN", 365.2, 0.42), ("STM", 52.18, 0.26), ("QCOM", 196.0, 0.24),
     ("FIX", 1664.0, 0.22), ("ETN", 443.5, 0.19), ("LITE", 946.6, 0.18), ("APH", 83.75, 0.16),
     ("SAP", 210.1, 0.05), ("VALE", 13.61, 0.03), ("TT", 440.0, -0.03), ("CCJ", 88.58, -0.04),
     ("ENPH", 33.04, -0.08), ("NTRA", 409.1, -0.23)],
    # Group 4 (N..P)
    [("ARM", 318.8, 1.65), ("GLW", 157.0, 0.69), ("TSEM", 223.0, 0.64), ("ASML", 1752.0, 0.55),
     ("DELL", 547.0, 0.53), ("AMAT", 483.1, 0.42), ("MRVL", 264.2, 0.41), ("AMD", 642.0, 0.37),
     ("VRT", 251.0, 0.35), ("SMCI", 42.38, 0.33), ("AXTI", 78.87, 0.32), ("JBL", 315.0, 0.30),
     ("KLAC", 190.3, 0.29), ("TER", 395.1, 0.29)],
    # Group 5 (R..T)
    [("PL", 18.37, 0.96), ("FSLY", 27.5, 0.82), ("ANET", 208.6, 0.56), ("UAL", 112.6, 0.51),
     ("ABNB", 152.5, 0.47), ("ON", 74.03, 0.43), ("CAT", 811.0, 0.31), ("MPWR", 1347.0, 0.28),
     ("DKS", 134.1, 0.27), ("ADI", 384.6, 0.24), ("ASTS", 62.3, 0.22), ("AGX", 365.0, 0.19),
     ("MRNA", 196.4, 0.13), ("CAVA", 53.85, 0.13)],
    # Group 6 (V..X)
    [("RKLB", 74.93, 0.39), ("WULF", 16.55, 0.36), ("NBIS", 247.9, 0.35), ("RIOT", 23.85, 0.35),
     ("MSTR", 163.2, 0.33), ("CLSK", 14.45, 0.27), ("OKLO", 38.84, 0.25), ("IREN", 46.82, 0.21),
     ("MP", 49.65, 0.16), ("MARA", 13.01, 0.15), ("COIN", 199.7, 0.00), ("IONQ", 45.02, -0.02),
     ("QBTS", 17.5, -0.03), ("AVAV", 158.3, -0.10)],
    # Group 7 (Z..AB)
    [("BABA", 110.7, 0.90), ("PDD", 78.16, 0.58), ("BIDU", 87.32, 0.22), ("JD", 26.58, 0.21),
     ("LYFT", 14.67, 0.19), ("SPY", 770.0, 0.36), ("QQQ", 745.7, 0.62), ("IWM", 282.5, 0.30),
     ("GLD", 393.7, 0.52), ("BITO", 11.36, 0.22), ("USO", 149.3, -2.48)],
    # Group 8 (AD..AF)
    [("PAAS", 48.05, 0.23), ("AEM", 195.6, 0.17), ("AU", 99.49, 0.12), ("AG", 19.0, 0.12),
     ("CDE", 19.16, 0.10), ("HL", 18.1, -0.02), ("RIO", 94.58, -0.11), ("KGC", 24.55, -0.12),
     ("RGLD", 253.1, -0.17), ("B", 42.43, -0.20), ("WPM", 143.8, -0.55), ("NEM", 121.0, -0.66),
     ("FNV", 258.7, -0.67)],
    # Group 9 (AH..AJ)
    [("SHEL", 95.8, 1.86), ("BP", 44.08, 0.76), ("RIG", 5.4, 0.56), ("APA", 43.01, 0.54),
     ("SLB", 51.11, 0.52), ("COP", 127.5, 0.31), ("VG", 13.02, 0.27), ("XOM", 160.3, 0.27),
     ("OXY", 57.11, 0.26), ("PBR", 20.61, 0.23), ("HAL", 32.29, 0.22), ("CF", 117.2, 0.19),
     ("PBR.A", 18.78, 0.10), ("ET", 20.21, 0.08)]
]

BOTTOM_BLOCK_DATA = [
    # Row 60+ Data
    [("CMCS", 21.85, 1.16), ("RACE", 407.1, 0.97), ("BTI", 55.52, 0.84), ("FLUT", 80.99, 0.78),
     ("MPC", 385.2, 0.45), ("COST", 895.7, 0.40), ("ADBE", 238.4, 0.38), ("MDT", 88.48, 0.20)],
    [("V", 367.8, 0.33), ("PFE", 28.41, 0.25), ("STLD", 0.0, 0.20), ("UNH", 375.8, 0.16),
     ("PYPL", 52.79, 0.10), ("JPM", 339.9, 0.03), ("MA", 567.7, 0.02), ("DOCU", 69.35, -0.01)],
    [("ISRG", 398.8, 0.50), ("TECK", 66.3, 0.49), ("MSFT", 497.2, 0.46), ("BHP", 84.94, 0.46),
     ("XYZ", 76.88, 0.38), ("AFRM", 71.95, 0.19), ("SAP", 210.2, -0.09), ("PANW", 392.0, -0.13)],
    [("ZS", 207.9, 2.25), ("TWLO", 294.0, 1.37), ("PLTR", 192.5, 0.69), ("SOFI", 16.9, 0.42),
     ("U", 41.49, 0.42), ("AAPL", 336.0, 0.31), ("CRWD", 260.8, 0.31), ("CSCO", 107.1, 0.31)],
    [("DKNG", 20.99, 0.72), ("DOW", 28.5, 0.33), ("RGTI", 16.57, 0.10), ("FIG", 21.39, 0.06),
     ("PATH", 12.65, 0.01), ("KTOS", 47.29, 0.00), ("TEM", 82.7, -0.02), ("HII", 267.0, -0.08)],
    [("CRCL", 92.97, 0.07), ("IONQ", 45.09, -0.01), ("QBTS", 17.6, -0.09), ("COIN", 200.0, -0.09),
     ("MARA", 13.03, -0.01), ("AVAV", 160.0, -0.26), ("MP", 49.83, -0.26), ("OKLO", 38.98, -0.32)],
    [("NIO", 3.61, -0.25), ("BIDU", 87.6, -0.38), ("JD", 26.66, -0.49), ("LYFT", 14.85, -0.69),
     ("PDD", 78.4, -0.86)],
    [("RIO", 94.64, 0.06), ("NEM", 122.2, -0.03), ("AG", 19.08, -0.26), ("ALB", 109.5, -0.29),
     ("HL", 18.26, -0.29), ("CDE", 19.29, -0.34), ("KGC", 24.74, -0.35), ("B", 42.85, -0.42)],
    [("CVX", 203.4, -0.03), ("EQNR", 42.36, -0.05), ("VLO", 379.7, -0.21), ("LNG", 275.0, -0.34),
     ("EXE", 88.35, -0.53), ("CVE", 30.94, -0.71), ("SLB", 51.3, -0.76), ("PBR", 20.74, -0.77)]
]

def build_excel_scanner_sheet(output_path: str = "Resulting_table.xlsx", use_rtd_formulas: bool = True):
    """
    Creates an Excel workbook that mirrors `analysis.xlsm - Resulting_table`
    with exact conditional styling and eSignal RTD formulas.
    """
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Resulting_table"
    ws.views.sheetView[0].showGridLines = True

    # Styling elements
    font_bold = Font(name="Calibri", size=10, bold=True)
    font_regular = Font(name="Calibri", size=10, bold=False)
    font_ticker = Font(name="Calibri", size=10, bold=True, color="000000")
    thin_border_side = Side(style='thin', color='D9D9D9')
    cell_border = Border(left=thin_border_side, right=thin_border_side, top=thin_border_side, bottom=thin_border_side)
    align_center = Alignment(horizontal="center", vertical="center")
    align_right = Alignment(horizontal="right", vertical="center")

    def apply_stock_row(row_idx, col_base, ticker, price, move_val):
        # Move Cell (Col B, F, J, N, R, V, Z, AD, AH)
        cell_move = ws.cell(row=row_idx, column=col_base)
        cell_ticker = ws.cell(row=row_idx, column=col_base + 1)
        cell_price = ws.cell(row=row_idx, column=col_base + 2)

        cell_ticker.value = ticker
        cell_ticker.font = font_ticker
        cell_ticker.alignment = align_center
        cell_ticker.border = cell_border

        if use_rtd_formulas:
            # Set eSignal RTD formulas
            # =RTD("esignal.rtd", , C2, "Last")
            ticker_ref = f"{get_column_letter(col_base + 1)}{row_idx}"
            cell_price.value = f'=RTD("esignal.rtd", , {ticker_ref}, "Last")'
            # Or if static price provided
            # cell_price.value = price
        else:
            cell_price.value = price

        cell_price.font = font_regular
        cell_price.alignment = align_right
        cell_price.number_format = "#,##0.00"
        cell_price.border = cell_border

        cell_move.value = move_val
        cell_move.font = font_bold
        cell_move.alignment = align_right
        cell_move.number_format = "0.00"
        cell_move.border = cell_border

        # Formatting and Anomaly Coloring based on user specs:
        # > 1.0 sigma -> Orange (#E67E22)
        # 0.8 - 1.2 sigma -> Vibrant Green (#27AE60)
        # 0.5 - 0.8 sigma -> Yellow (#FCF3CF)
        # 0.3 - 0.5 sigma -> Translucent Light Green (#D4EFDF)
        abs_m = abs(move_val)
        fill_hex = None
        font_c = "000000"

        if abs_m >= 1.0 or move_val >= 1.0:
            fill_hex = "E67E22"  # Orange
            font_c = "FFFFFF"
        elif abs_m >= 0.70:
            fill_hex = "27AE60"  # Vibrant Green
            font_c = "FFFFFF"
        elif abs_m >= 0.50:
            fill_hex = "FCF3CF"  # Yellow
            font_c = "7D6608"
        elif abs_m >= 0.30:
            fill_hex = "D4EFDF"  # Translucent Soft Green
            font_c = "145A32"

        if fill_hex:
            cell_move.fill = PatternFill(start_color=fill_hex, end_color=fill_hex, fill_type="solid")
            cell_move.font = Font(name="Calibri", size=10, bold=True, color=font_c)

    # 1. Populate Top Block (Rows 2..15)
    for grp_idx, col_info in enumerate(COL_OFFSETS):
        col_base = col_info[0]
        stocks = TOP_BLOCK_DATA[grp_idx]
        for row_offset, (t, p, m) in enumerate(stocks):
            apply_stock_row(2 + row_offset, col_base, t, p, m)

    # 2. Populate Bottom Block (Rows 60..67)
    for grp_idx, col_info in enumerate(COL_OFFSETS):
        col_base = col_info[0]
        stocks = BOTTOM_BLOCK_DATA[grp_idx]
        for row_offset, (t, p, m) in enumerate(stocks):
            apply_stock_row(60 + row_offset, col_base, t, p, m)

    # Column widths formatting
    for col_info in COL_OFFSETS:
        col_b = col_info[0]
        ws.column_dimensions[get_column_letter(col_b)].width = 7.5   # Move %
        ws.column_dimensions[get_column_letter(col_b + 1)].width = 7.5 # Ticker
        ws.column_dimensions[get_column_letter(col_b + 2)].width = 9.0 # Price
        # Spacer column width
        if col_b > 2:
            ws.column_dimensions[get_column_letter(col_b - 1)].width = 2.0

    wb.save(output_path)
    print(f"Successfully created Excel scanner template: {output_path}")
    return output_path

if __name__ == "__main__":
    build_excel_scanner_sheet("Resulting_table_test.xlsx", use_rtd_formulas=False)
