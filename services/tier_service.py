import math
import io
import datetime
from typing import Dict, List, Any, Optional
import numpy as np
import pandas as pd
import psycopg
import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter

from config import DB_CONFIG

# Constant for Garman-Klass volatility: 2 * ln(2) - 1
GK_CONST = 2.0 * math.log(2.0) - 1.0  # ~0.38614728


def calculate_garman_klass_series(high: np.ndarray, low: np.ndarray, close: np.ndarray, open_prices: np.ndarray) -> np.ndarray:
    """
    Computes daily Garman-Klass variance series:
    sigma_t^2 = 0.5 * (ln(High/Low))^2 - (2*ln(2) - 1) * (ln(Close/Open))^2
    """
    # Guard against zero or negative values
    h_safe = np.maximum(high, 1e-6)
    l_safe = np.maximum(low, 1e-6)
    c_safe = np.maximum(close, 1e-6)
    o_safe = np.maximum(open_prices, 1e-6)

    h_l = np.maximum(h_safe / l_safe, 1.0)
    c_o = c_safe / o_safe

    term1 = 0.5 * (np.log(h_l)) ** 2
    term2 = GK_CONST * (np.log(c_o)) ** 2

    # Prevent negative values due to numerical precision
    daily_var = np.maximum(term1 - term2, 0.0)
    return daily_var


def fetch_ohlc_for_tier(tickers: List[str], lookback_days: int = 20) -> pd.DataFrame:
    """
    Optimized database query fetching daily OHLC for requested tickers
    over the lookback period from PostgreSQL 'public.daily'.
    """
    if not tickers:
        return pd.DataFrame()

    cleaned = list(dict.fromkeys([str(t).strip().upper() for t in tickers if t and str(t).strip()]))
    if not cleaned:
        return pd.DataFrame()

    # Need enough calendar days to cover lookback_days trading days
    cal_days = max(int(lookback_days * 2.8), 60)

    try:
        with psycopg.connect(**DB_CONFIG) as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT UPPER(t.ticker) AS ticker,
                           d.date::text AS date,
                           d.open::float AS open,
                           d.high::float AS high,
                           d.low::float AS low,
                           d.close::float AS close,
                           d.volume::float AS volume
                    FROM daily d
                    JOIN ticker t ON t.id = d.id_ticker
                    WHERE UPPER(t.ticker) = ANY(%s)
                      AND d.date >= CURRENT_DATE - (%s || ' days')::interval
                    ORDER BY t.ticker, d.date ASC
                """, (cleaned, cal_days))
                rows = cur.fetchall()

        if not rows:
            return pd.DataFrame()

        df = pd.DataFrame(rows, columns=["ticker", "date", "open", "high", "low", "close", "volume"])
        return df
    except Exception as e:
        print(f"[TierService] Database query error: {e}")
        return pd.DataFrame()


def apply_share_rounding(exact_val: float, mode: str = "1") -> Any:
    """Applies rounding policy to exact shares."""
    if exact_val <= 0 or math.isnan(exact_val):
        return 1

    if mode == "exact":
        return round(float(exact_val), 2)
    elif mode == "5":
        return max(5, int(round(exact_val / 5.0) * 5))
    elif mode == "10":
        return max(10, int(round(exact_val / 10.0) * 10))
    elif mode == "floor":
        return max(1, math.floor(exact_val))
    elif mode == "ceil":
        return max(1, math.ceil(exact_val))
    else:  # default "1": nearest whole integer
        return max(1, int(round(exact_val)))


def calculate_tier_position_sizing(
    benchmark: str = "",
    benchmark_shares: float = 5.0,
    lookback_days: int = 20,
    tickers: Optional[List[str]] = None,
    rounding_mode: str = "1"
) -> Dict[str, Any]:
    """
    Computes Garman-Klass Volatility and Risk-Parity Tier Position Sizing
    relative to the benchmark stock.
    """
    benchmark = (benchmark or "").strip().upper()
    try:
        benchmark_shares = float(benchmark_shares) if benchmark_shares else 5.0
        if benchmark_shares <= 0:
            benchmark_shares = 5.0
    except (ValueError, TypeError):
        benchmark_shares = 5.0

    try:
        lookback_days = int(lookback_days) if lookback_days else 20
        lookback_days = max(2, min(lookback_days, 252))
    except (ValueError, TypeError):
        lookback_days = 20

    if tickers is None:
        clean_tickers = []
    else:
        clean_tickers = list(dict.fromkeys([str(t).strip().upper() for t in tickers if t and str(t).strip()]))

    bench_data = {
        "ticker": benchmark,
        "price": 0.0,
        "gk_daily_pct": 0.0,
        "gk_annual_pct": 0.0,
        "base_shares": benchmark_shares,
        "notional": 0.0,
        "dollar_risk": 0.0,
        "trading_days": 0,
        "as_of": ""
    }

    if not benchmark:
        return {
            "status": "empty",
            "benchmark": bench_data,
            "parameters": {
                "benchmark": "",
                "benchmark_shares": benchmark_shares,
                "lookback_days": lookback_days,
                "rounding_mode": rounding_mode
            },
            "summary": {
                "total_tickers": 0,
                "missing_count": 0,
                "missing_tickers": [],
                "avg_gk_daily_pct": 0.0,
                "avg_gk_annual_pct": 0.0,
                "total_notional_t1": 0.0,
                "total_risk_t1": 0.0
            },
            "items": [],
            "results": []
        }

    all_query_tickers = list(dict.fromkeys([benchmark] + clean_tickers))
    df = fetch_ohlc_for_tier(all_query_tickers, lookback_days=lookback_days)

    # 1. Calculate Benchmark Garman-Klass Volatility & Risk
    bench_group = df[df["ticker"] == benchmark].sort_values("date") if not df.empty else pd.DataFrame()

    if not bench_group.empty and len(bench_group) >= 2:
        g = bench_group.tail(lookback_days)
        h = g["high"].values
        l = g["low"].values
        c = g["close"].values
        o = g["open"].values

        daily_var = calculate_garman_klass_series(h, l, c, o)
        gk_daily = float(np.sqrt(np.mean(daily_var)))
        gk_annual = float(gk_daily * np.sqrt(252))
        price = float(c[-1])

        notional = price * benchmark_shares
        dollar_risk = notional * gk_daily

        bench_data.update({
            "price": round(price, 2),
            "gk_daily_pct": round(gk_daily * 100, 2),
            "gk_annual_pct": round(gk_annual * 100, 2),
            "notional": round(notional, 2),
            "dollar_risk": round(dollar_risk, 2),
            "trading_days": len(g),
            "as_of": str(g["date"].iloc[-1])
        })

    # Benchmark risk target (dollar volatility of benchmark position)
    bench_risk_target = bench_data["dollar_risk"]
    bench_gk_daily_dec = (bench_data["gk_daily_pct"] / 100.0) if bench_data["gk_daily_pct"] > 0 else 0.02

    # 2. Calculate Tier Sizing for each target stock
    items = []
    missing_tickers = []

    for sym in clean_tickers:
        g_sym = df[df["ticker"] == sym].sort_values("date") if not df.empty else pd.DataFrame()
        if g_sym.empty or len(g_sym) < 2:
            missing_tickers.append(sym)
            continue

        g = g_sym.tail(lookback_days)
        h = g["high"].values
        l = g["low"].values
        c = g["close"].values
        o = g["open"].values

        daily_var = calculate_garman_klass_series(h, l, c, o)
        gk_daily = float(np.sqrt(np.mean(daily_var)))
        gk_annual = float(gk_daily * np.sqrt(252))
        price = float(c[-1])

        # Dollar Volatility per 1 Share
        dollar_vol_per_share = price * gk_daily

        if dollar_vol_per_share > 0 and bench_risk_target > 0:
            exact_shares = bench_risk_target / dollar_vol_per_share
        elif dollar_vol_per_share > 0:
            exact_shares = (benchmark_shares * bench_data["price"]) / price
        else:
            exact_shares = 1.0

        tier1_shares = apply_share_rounding(exact_shares, rounding_mode)
        tier2_shares = round(tier1_shares * 2, 2) if rounding_mode == "exact" else int(tier1_shares * 2)
        tier3_shares = round(tier1_shares * 3, 2) if rounding_mode == "exact" else int(tier1_shares * 3)
        tier05_shares = round(tier1_shares * 0.5, 2) if rounding_mode == "exact" else max(1, int(round(tier1_shares * 0.5)))

        notional_t1 = round(float(tier1_shares * price), 2)
        actual_risk_t1 = round(float(tier1_shares * dollar_vol_per_share), 2)

        parity_pct = round((actual_risk_t1 / bench_risk_target) * 100, 1) if bench_risk_target > 0 else 100.0
        vol_ratio = round((gk_daily / bench_gk_daily_dec), 2) if bench_gk_daily_dec > 0 else 1.0

        items.append({
            "ticker": sym,
            "price": round(price, 2),
            "gk_daily_pct": round(gk_daily * 100, 2),
            "gk_annual_pct": round(gk_annual * 100, 2),
            "dollar_vol_1sh": round(dollar_vol_per_share, 2),
            "exact_shares": round(exact_shares, 2),
            "tier1_shares": tier1_shares,
            "tier2_shares": tier2_shares,
            "tier3_shares": tier3_shares,
            "tier05_shares": tier05_shares,
            "notional": notional_t1,
            "actual_risk": actual_risk_t1,
            "parity_pct": parity_pct,
            "vol_ratio": vol_ratio,
            "trading_days": len(g),
            "as_of": str(g["date"].iloc[-1])
        })

    # Summary Statistics
    total_analyzed = len(items)
    avg_gk_daily = round(float(np.mean([x["gk_daily_pct"] for x in items])), 2) if items else 0.0
    avg_gk_annual = round(float(np.mean([x["gk_annual_pct"] for x in items])), 2) if items else 0.0
    total_notional_t1 = round(float(sum([x["notional"] for x in items])), 2) if items else 0.0
    total_risk_t1 = round(float(sum([x["actual_risk"] for x in items])), 2) if items else 0.0

    return {
        "status": "ok",
        "benchmark": bench_data,
        "parameters": {
            "benchmark": benchmark,
            "benchmark_shares": benchmark_shares,
            "lookback_days": lookback_days,
            "rounding_mode": rounding_mode
        },
        "summary": {
            "total_tickers": total_analyzed,
            "missing_count": len(missing_tickers),
            "missing_tickers": missing_tickers,
            "avg_gk_daily_pct": avg_gk_daily,
            "avg_gk_annual_pct": avg_gk_annual,
            "total_notional_t1": total_notional_t1,
            "total_risk_t1": total_risk_t1
        },
        "items": items,
        "results": items
    }


def build_tier_excel(tier_data: Dict[str, Any]) -> io.BytesIO:
    """
    Builds an Excel workbook for Tier position sizing containing only Ticker and Tier 1 Shares.
    """
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Tier Shares"
    ws.views.sheetView[0].showGridLines = True

    items = tier_data.get("items", [])

    # Colors
    header_fill = PatternFill(start_color="13151C", end_color="13151C", fill_type="solid")
    header_font = Font(name="Segoe UI", size=10, bold=True, color="FFFFFF")
    accent_green_fill = PatternFill(start_color="1B4D3E", end_color="1B4D3E", fill_type="solid")
    accent_green_font = Font(name="Segoe UI", size=10, bold=True, color="4ADE80")

    border_thin = Side(border_style="thin", color="CBD5E1")
    cell_border = Border(left=border_thin, right=border_thin, top=border_thin, bottom=border_thin)

    headers = [
        ("ТИКЕР", Alignment(horizontal="center"), header_fill, header_font),
        ("ТИР 1 (АКЦИИ)", Alignment(horizontal="center"), accent_green_fill, accent_green_font)
    ]

    header_row = 1
    ws.row_dimensions[header_row].height = 26

    for col_idx, (h_title, h_align, h_fill, h_font) in enumerate(headers, 1):
        cell = ws.cell(row=header_row, column=col_idx, value=h_title)
        cell.fill = h_fill
        cell.font = h_font
        cell.alignment = Alignment(horizontal=h_align.horizontal, vertical="center")
        cell.border = cell_border

    current_row = 2
    for item in items:
        ws.row_dimensions[current_row].height = 20

        c_ticker = ws.cell(row=current_row, column=1, value=item.get("ticker", ""))
        c_ticker.alignment = Alignment(horizontal="center", vertical="center")
        c_ticker.font = Font(name="Segoe UI", size=10, bold=True)
        c_ticker.border = cell_border

        t1_val = item.get("tier1_shares", 0)
        c_shares = ws.cell(row=current_row, column=2, value=t1_val)
        c_shares.alignment = Alignment(horizontal="center", vertical="center")
        c_shares.font = Font(name="Segoe UI", size=10, bold=True, color="166534")
        c_shares.border = cell_border
        if isinstance(t1_val, int):
            c_shares.number_format = "#,##0"
        else:
            c_shares.number_format = "0.00"

        current_row += 1

    ws.column_dimensions["A"].width = 16
    ws.column_dimensions["B"].width = 20

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf

