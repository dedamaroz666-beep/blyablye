import os
import datetime
from typing import Dict, List, Any, Optional
import pandas as pd
import numpy as np
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

import io
import re
import sqlite3
import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter

from config import BASE_DIR, BENCHMARKS, CACHE_DB_PATH
from database import (
    get_available_tickers,
    get_database_status,
    get_stock_data,
    get_intraday_chart_data,
    get_ticker_reports,
    get_ticker_dividends_db,
    save_ticker_dividends_db,
    get_company_profile_db,
    save_company_profile_db,
    get_postgres_connection,
    check_postgres_health,
    get_session_bounds
)
from calculations import (
    calculate_all_periods,
    calculate_groups_analysis
)
from services.polygon_service import polygon_client
from services.fmp_service import fmp_client
from services.earnings_reaction_service import earnings_reaction_client, is_report_already_released, parse_earnings_session
from services.quant_service import quant_client
from services.earnings_extra_service import get_ticker_earnings_extra_map, find_matching_earnings_extra
from services.datalens_service import calculate_hold_move_dates
from services.tier_service import calculate_tier_position_sizing, build_tier_excel

app = FastAPI(
    title="Helion Financial Analysis Platform",
    description="Quantitative ticker analytics, dual TradingView charts, earnings moves, dividends, and correlation matrix.",
    version="2.0.0"
)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Static files directory
STATIC_DIR = BASE_DIR / "static"
os.makedirs(STATIC_DIR, exist_ok=True)
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


# ============================================================
# ROOT PAGE
# ============================================================

@app.get("/", response_class=HTMLResponse)
async def get_index():
    index_file = STATIC_DIR / "index.html"
    if index_file.exists():
        return FileResponse(str(index_file))
    return HTMLResponse("<h1>Helion Quant Analysis Platform API Running</h1><p>Visit /docs for Swagger API</p>")


# ============================================================
# SYSTEM STATUS & TICKERS
# ============================================================

@app.get("/api/status")
async def api_status():
    db_stat = get_database_status()
    return {
        "status": "online",
        "database": db_stat,
        "timestamp": datetime.datetime.now().isoformat(),
        "benchmarks": BENCHMARKS
    }


@app.get("/api/tickers")
async def api_tickers(q: Optional[str] = None):
    all_tickers = get_available_tickers()
    if q:
        query = q.strip().upper()
        filtered = [t for t in all_tickers if query in t]
        return {"tickers": filtered[:50], "total": len(filtered)}
    return {"tickers": all_tickers, "total": len(all_tickers)}


# ============================================================
# COMPANY PROFILE & PEERS
# ============================================================

def compute_stock_volatility(ticker: str) -> Optional[float]:
    try:
        df_daily = get_stock_data(ticker)
        if df_daily.empty:
            poly_bars = polygon_client.fetch_daily_bars(ticker, start_date="2023-01-01")
            if poly_bars:
                df_daily = pd.DataFrame(poly_bars)
        if not df_daily.empty and len(df_daily) >= 15:
            df_sorted = df_daily.sort_values("date").tail(252)
            rets = df_sorted["close"].pct_change().dropna()
            if len(rets) >= 10:
                ann_vol = float(rets.std() * np.sqrt(252) * 100.0)
                return round(ann_vol, 1)
    except Exception as e:
        print(f"Error computing volatility for {ticker}:", e)
    return None


@app.get("/api/ticker/{ticker}/profile")
async def api_company_profile(ticker: str):
    ticker = ticker.strip().upper()
    cached = get_company_profile_db(ticker)
    vol = compute_stock_volatility(ticker)
    
    peers_list = fmp_client.get_stock_peers(ticker)
    business_peers = [
        {
            "symbol": p["symbol"].strip().upper(),
            "companyName": p.get("companyName", p["symbol"]),
            "price": p.get("price")
        }
        for p in peers_list if p.get("symbol") and p.get("symbol").strip().upper() != ticker
    ]
    peer_symbols = [p["symbol"] for p in business_peers]

    if cached and cached.get("description_ru"):
        raw_db_peers = cached.get("peers", [])
        if isinstance(raw_db_peers, str):
            raw_db_peers = [p.strip() for p in raw_db_peers.split(",") if p.strip()]
        cached["peers"] = peer_symbols or raw_db_peers
        cached["business_peers"] = business_peers
        cached["volatility"] = vol or cached.get("volatility")
        cached["description"] = cached.get("description_ru")
        return cached

    profile = fmp_client.get_company_profile(ticker)
    profile["peers"] = peer_symbols
    profile["business_peers"] = business_peers
    profile["volatility"] = vol
    profile["description"] = profile.get("description") or profile.get("description_ru")

    save_company_profile_db(ticker, profile)
    return profile


# ============================================================
# DUAL CHARTS DATA
# ============================================================

@app.get("/api/ticker/{ticker}/chart/daily")
async def api_daily_chart(ticker: str):
    """
    Returns daily OHLCV candlestick series + Earnings 'E' and Dividends 'D' event markers.
    """
    ticker = ticker.strip().upper()

    # 1. Fetch daily candles from DB or Polygon
    df = get_stock_data(ticker)
    candles = []

    if not df.empty:
        df["date_str"] = df["date"].dt.strftime("%Y-%m-%d")
        df = df.sort_values("date_str")
        for _, r in df.iterrows():
            candles.append({
                "time": r["date_str"],
                "open": float(r["open"]),
                "high": float(r["high"]),
                "low": float(r["low"]),
                "close": float(r["close"]),
                "volume": float(r["volume"]) if pd.notnull(r["volume"]) else 0.0
            })
    else:
        poly_bars = polygon_client.fetch_daily_bars(ticker, start_date="2020-01-01")
        for b in poly_bars:
            candles.append({
                "time": b["date"],
                "open": b["open"],
                "high": b["high"],
                "low": b["low"],
                "close": b["close"],
                "volume": b["volume"]
            })

    # 2. Build Event Markers
    markers = []
    marker_details = {}

    candle_dates = {c["time"] for c in candles}
    sorted_candle_dates = sorted(list(candle_dates))

    def find_nearest_candle_date(target_date: str) -> Optional[str]:
        if target_date in candle_dates:
            return target_date
        if not sorted_candle_dates or target_date < sorted_candle_dates[0] or target_date > sorted_candle_dates[-1]:
            return None
        import bisect
        idx = bisect.bisect_left(sorted_candle_dates, target_date)
        if idx < len(sorted_candle_dates):
            cand = sorted_candle_dates[idx]
            try:
                d_target = datetime.date.fromisoformat(target_date)
                d_cand = datetime.date.fromisoformat(cand)
                if abs((d_cand - d_target).days) <= 4:
                    return cand
            except Exception:
                pass
        return None

    # A. Earnings Markers ('E')
    seen_earnings_dates = set()
    seen_candle_dates_list = []
    extra_map = get_ticker_earnings_extra_map(ticker)
    db_reports_df = get_ticker_reports(ticker)

    # 1. Primary source: PostgreSQL reports (Benzinga)
    if not db_reports_df.empty:
        for _, r in db_reports_df.iterrows():
            raw_date = str(r["date"])
            extra_info = find_matching_earnings_extra(extra_map, raw_date)
            if not extra_info and r.get("move_date"):
                extra_info = find_matching_earnings_extra(extra_map, str(r["move_date"]))
            session_name = parse_earnings_session(r.get("time")).upper()
            check_dict = {
                "date": raw_date,
                "time": session_name.lower(),
                "eps_actual": extra_info.get("eps_actual") if extra_info else None,
                "revenue_actual": extra_info.get("revenue_actual") if extra_info else None
            }
            if not is_report_already_released(check_dict):
                continue

            c_date = find_nearest_candle_date(raw_date)
            if not c_date and r.get("move_date"):
                c_date = find_nearest_candle_date(str(r["move_date"]))
            if not c_date:
                continue

            try:
                d_c = datetime.date.fromisoformat(c_date)
                if any(abs((d_c - prev_d).days) < 45 for prev_d in seen_candle_dates_list):
                    continue
                seen_candle_dates_list.append(d_c)
            except Exception:
                if c_date in seen_earnings_dates:
                    continue

            seen_earnings_dates.add(c_date)
            m_id = f"e_{c_date}"
            quarter_name = f"{r.get('fiscal_period', '')} {r.get('fiscal_year', '')}".strip()

            markers.append({
                "time": c_date,
                "position": "belowBar",
                "color": "#10b981",
                "shape": "square",
                "text": "E",
                "id": m_id
            })
            marker_details[m_id] = {
                "type": "Earnings",
                "date": c_date,
                "session": session_name,
                "quarter": quarter_name,
                "eps_actual": extra_info.get("eps_actual") if extra_info else None,
                "eps_estimate": extra_info.get("eps_estimate") if extra_info else None,
                "eps_surprise_pct": extra_info.get("eps_surprise_pct") if extra_info else None,
                "revenue_actual": extra_info.get("revenue_actual") if extra_info else None,
                "revenue_estimate": extra_info.get("revenue_estimate") if extra_info else None
            }

    # 2. Fallback or supplementary: FMP reports (for missing tickers or unreleased future announcements)
    earnings_list = fmp_client.get_earnings_history(ticker, limit=30)
    for earn in earnings_list:
        raw_date = earn.get("date")
        if not raw_date:
            continue
        c_date = find_nearest_candle_date(raw_date)
        if not c_date:
            continue
        try:
            d_c = datetime.date.fromisoformat(c_date)
            if any(abs((d_c - prev_d).days) < 45 for prev_d in seen_candle_dates_list):
                continue
            seen_candle_dates_list.append(d_c)
        except Exception:
            if c_date in seen_earnings_dates:
                continue

        extra_info = find_matching_earnings_extra(extra_map, raw_date)
        session_name = parse_earnings_session(earn.get("time")).upper()
        check_dict = {
            "date": raw_date,
            "time": session_name.lower(),
            "eps_actual": earn.get("eps_actual") if earn.get("eps_actual") is not None else (extra_info.get("eps_actual") if extra_info else None),
            "revenue_actual": earn.get("revenue_actual") if earn.get("revenue_actual") is not None else (extra_info.get("revenue_actual") if extra_info else None)
        }
        if not is_report_already_released(check_dict):
            continue

        seen_earnings_dates.add(c_date)
        m_id = f"e_{c_date}"
        markers.append({
            "time": c_date,
            "position": "belowBar",
            "color": "#10b981",
            "shape": "square",
            "text": "E",
            "id": m_id
        })
        marker_details[m_id] = {
            "type": "Earnings",
            "date": c_date,
            "session": session_name,
            "eps_actual": check_dict["eps_actual"],
            "eps_estimate": earn.get("eps_estimate") if earn.get("eps_estimate") is not None else (extra_info.get("eps_estimate") if extra_info else None),
            "eps_surprise_pct": earn.get("eps_surprise_pct") if earn.get("eps_surprise_pct") is not None else (extra_info.get("eps_surprise_pct") if extra_info else None),
            "revenue_actual": check_dict["revenue_actual"],
            "revenue_estimate": earn.get("revenue_estimate") if earn.get("revenue_estimate") is not None else (extra_info.get("revenue_estimate") if extra_info else None)
        }

    # B. Dividends Markers ('D')
    seen_dividend_dates = set()
    div_list = fmp_client.get_dividends_history(ticker, limit=30)
    if not div_list:
        div_list = polygon_client.fetch_dividends(ticker)
    for div in div_list:
        raw_date = div.get("ex_date") or div.get("date")
        if not raw_date:
            continue
        c_date = find_nearest_candle_date(raw_date)
        if not c_date or c_date in seen_dividend_dates:
            continue
        seen_dividend_dates.add(c_date)
        m_id = f"d_{c_date}"
        markers.append({
            "time": c_date,
            "position": "belowBar",
            "color": "#d97706",
            "shape": "circle",
            "text": "D",
            "id": m_id
        })
        marker_details[m_id] = {
            "type": "Dividend",
            "ex_date": c_date,
            "amount": div.get("amount") or div.get("dividend"),
            "pay_date": div.get("pay_date") or div.get("paymentDate"),
            "yield": div.get("yield"),
            "frequency": div.get("frequency", "Quarterly")
        }

    return {
        "ticker": ticker,
        "candles": candles,
        "markers": markers,
        "marker_details": marker_details
    }


@app.get("/api/ticker/{ticker}/chart/intraday")
async def api_intraday_chart(
    ticker: str,
    date: Optional[str] = None,
    multiplier: int = Query(1, ge=1, le=60),
    days_back: int = Query(30, ge=1, le=90)
):
    """
    Returns continuous multi-day 1m (or custom multiplier) intraday candlestick series around the requested date,
    allowing full scrolling to yesterday, day before, today, and future days.
    """
    ticker = ticker.strip().upper()
    today_dt = datetime.date.today()
    if date:
        try:
            req_dt = datetime.date.fromisoformat(date)
        except Exception:
            req_dt = today_dt
    else:
        req_dt = today_dt

    # 30 days window around requested date so user can freely scroll backwards & forwards
    start_date = (req_dt - datetime.timedelta(days=days_back)).isoformat()
    end_date = min(today_dt, req_dt + datetime.timedelta(days=10)).isoformat()

    # 1. Try DB
    df = get_intraday_chart_data(ticker, start_date=start_date, end_date=end_date)
    candles = []

    if not df.empty:
        # Sort chronologically by date and time
        df = df.sort_values(["date", "time"])
        if multiplier > 1:
            try:
                df["_dt"] = pd.to_datetime(df["datetime_et"])
                df = df.set_index("_dt")
                agg_dict = {
                    "open": "first",
                    "high": "max",
                    "low": "min",
                    "close": "last",
                    "volume": "sum",
                    "session_type": "first",
                    "date": "first",
                    "ticker": "first"
                }
                resampled = df.resample(f"{multiplier}min").agg(agg_dict).dropna(subset=["close"]).reset_index()
                resampled["time_str"] = resampled["_dt"].dt.strftime("%H:%M")
                resampled["time"] = resampled["_dt"].dt.strftime("%H:%M:%S")
                resampled["datetime_et"] = resampled["_dt"]
                df = resampled
            except Exception as ex:
                print(f"[Intraday] Resample error for {ticker}: {ex}")
                if "_dt" in df.columns:
                    df = df.reset_index(drop=True)

        df["dt_str"] = df["date"].astype(str) + " " + df["time"].astype(str)
        try:
            dt_series = pd.to_datetime(df["datetime_et"])
            unit = getattr(dt_series.dtype, "unit", "us")
            divisor = 10**9 if unit == "ns" else (10**6 if unit == "us" else (10**3 if unit == "ms" else 1))
            ts_series = (dt_series.astype("int64") // divisor).astype(int)
        except Exception:
            dt_series = pd.to_datetime(df["dt_str"])
            unit = getattr(dt_series.dtype, "unit", "us")
            divisor = 10**9 if unit == "ns" else (10**6 if unit == "us" else (10**3 if unit == "ms" else 1))
            ts_series = (dt_series.astype("int64") // divisor).astype(int)
        df["timestamp"] = ts_series
        df["open"] = df["open"].astype(float)
        df["high"] = df["high"].astype(float)
        df["low"] = df["low"].astype(float)
        df["close"] = df["close"].astype(float)
        df["volume"] = df["volume"].astype(float)
        df["date"] = df["date"].astype(str)
        if "session_type" not in df.columns:
            df["session_type"] = "regular"

        candles = df[[
            "dt_str", "timestamp", "date", "time_str",
            "open", "high", "low", "close", "volume", "session_type"
        ]].rename(columns={"dt_str": "time"}).to_dict("records")
    else:
        # Fallback to Polygon
        poly_bars = polygon_client.fetch_intraday_bars(
            ticker,
            start_date=start_date,
            end_date=end_date,
            multiplier=multiplier,
            timespan="minute"
        )
        for b in poly_bars:
            candles.append({
                "time": b["datetime_et"],
                "timestamp": b.get("timestamp"),
                "date": b.get("date"),
                "time_str": b["time_str"],
                "open": b["open"],
                "high": b["high"],
                "low": b["low"],
                "close": b["close"],
                "volume": b["volume"],
                "session_type": b["session_type"]
            })

    return {
        "ticker": ticker,
        "date": req_dt.isoformat(),
        "start_date": start_date,
        "end_date": end_date,
        "multiplier": multiplier,
        "candles": candles,
        "count": len(candles)
    }


# ============================================================
# EARNINGS REACTION MATRIX (8 QUARTERS)
# ============================================================

@app.get("/api/ticker/{ticker}/earnings-moves")
async def api_earnings_moves(
    ticker: str,
    window: str = Query("04:00_09:30", pattern="^(04:00_07:00|04:00_09:00|04:00_09:30)$"),
    limit: int = Query(8, ge=1, le=16)
):
    """
    Returns the 8-quarter Earnings Reaction Matrix with 1-D postmarket %, 0-D premarket % (interval filtered), and intraday metrics.
    """
    ticker = ticker.strip().upper()
    matrix = earnings_reaction_client.get_earnings_reaction_matrix(
        ticker=ticker,
        limit=limit,
        premarket_window=window
    )
    return {
        "ticker": ticker,
        "premarket_window": window,
        "reports_count": len(matrix),
        "matrix": matrix
    }


# ============================================================
# EARNINGS REACTION MATRIX EXPORT & CORRELATIONS
# ============================================================

METRIC_LABELS = {
    "close_to_close": "Весь день (Close-to-Close %)",
    "premarket": "Премаркет %",
    "intraday": "Интрадей (Open → Close %)",
    "postmarket": "Постмаркет %"
}

@app.get("/api/ticker/{ticker}/earnings-moves/export")
async def export_earnings_moves(
    ticker: str,
    r_minus_1_metric: str = Query("close_to_close"),
    r_0_metric: str = Query("close_to_close"),
    r_plus_1_metric: str = Query("close_to_close"),
    r_plus_2_metric: str = Query("close_to_close")
):
    """
    Exports the 8-quarter Earnings Reaction Matrix to a styled Excel spreadsheet with selected metrics.
    """
    ticker = ticker.strip().upper()
    matrix = earnings_reaction_client.get_earnings_reaction_matrix(ticker=ticker, limit=8)
    if not matrix:
        raise HTTPException(status_code=400, detail="Нет данных по отчетам для экспорта.")

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = f"{ticker} Earnings Matrix"

    # Title row
    ws.merge_cells("A1:L1")
    title_cell = ws["A1"]
    title_cell.value = f"CHEPUH / FinWhale • Матрица движений на отчетности: {ticker} (Последние 8 кварталов)"
    title_cell.font = Font(name="Calibri", size=13, bold=True, color="1E293B")
    title_cell.alignment = Alignment(horizontal="left", vertical="center")
    ws.row_dimensions[1].height = 28

    header_fill = PatternFill(start_color="1E293B", end_color="1E293B", fill_type="solid")
    header_font = Font(name="Calibri", size=10, bold=True, color="FFFFFF")
    header_alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    border_thin = Side(border_style="thin", color="CBD5E1")
    cell_border = Border(left=border_thin, right=border_thin, top=border_thin, bottom=border_thin)

    pos_fill = PatternFill(start_color="E8F5E9", end_color="E8F5E9", fill_type="solid")
    pos_font = Font(name="Calibri", size=10, color="1B5E20", bold=True)
    neg_fill = PatternFill(start_color="FFEBEE", end_color="FFEBEE", fill_type="solid")
    neg_font = Font(name="Calibri", size=10, color="B71C1C", bold=True)

    col_metrics = {
        "R-1": r_minus_1_metric,
        "R": r_0_metric,
        "R+1": r_plus_1_metric,
        "R+2": r_plus_2_metric
    }

    headers = [
        "Квартал",
        "Дата отчета",
        "Сессия",
        "EPS Факт ($)",
        "EPS Ожид. ($)",
        "Сюрприз EPS (%)",
        "Выручка Факт",
        "Выручка Ожид.",
        f"R-1 ({METRIC_LABELS.get(r_minus_1_metric, r_minus_1_metric)})",
        f"R ({METRIC_LABELS.get(r_0_metric, r_0_metric)})",
        f"R+1 ({METRIC_LABELS.get(r_plus_1_metric, r_plus_1_metric)})",
        f"R+2 ({METRIC_LABELS.get(r_plus_2_metric, r_plus_2_metric)})"
    ]

    ws.append(headers)
    ws.row_dimensions[2].height = 26

    for col_idx in range(1, len(headers) + 1):
        cell = ws.cell(row=2, column=col_idx)
        cell.fill = header_fill
        cell.font = header_font
        cell.alignment = header_alignment
        cell.border = cell_border

    # Day metric values collector for summary stats
    day_values = {"R-1": [], "R": [], "R+1": [], "R+2": []}

    for row_idx, r in enumerate(matrix, start=3):
        days_dict = r.get("days", {})
        row_cells = [
            r.get("quarter", "—"),
            r.get("date", "—"),
            r.get("session", "—"),
            r.get("eps_actual"),
            r.get("eps_estimate"),
            (f"{r.get('eps_surprise_pct'):+.2f}%" if r.get("eps_surprise_pct") is not None else "—"),
            r.get("revenue_actual"),
            r.get("revenue_estimate")
        ]

        for d_key in ["R-1", "R", "R+1", "R+2"]:
            m_name = col_metrics[d_key]
            d_info = days_dict.get(d_key, {})
            val = d_info.get(m_name)
            if val is not None and isinstance(val, (int, float)):
                day_values[d_key].append(float(val))
                row_cells.append(f"{val:+.2f}%")
            else:
                row_cells.append("—")

        ws.append(row_cells)
        ws.row_dimensions[row_idx].height = 20

        # Style data row
        for col_idx in range(1, len(headers) + 1):
            cell = ws.cell(row=row_idx, column=col_idx)
            cell.border = cell_border
            cell.alignment = Alignment(horizontal="center", vertical="center")
            if col_idx in [9, 10, 11, 12]:
                val_str = str(cell.value or "")
                if val_str.startswith("+"):
                    cell.fill = pos_fill
                    cell.font = pos_font
                elif val_str.startswith("-"):
                    cell.fill = neg_fill
                    cell.font = neg_font

    # Summary Statistics rows (Mean, Median, Max, Min, Win Rate)
    def calc_stats(vals):
        if not vals:
            return "—", "—", "—", "—", "—"
        mean_v = np.mean(vals)
        med_v = np.median(vals)
        max_v = np.max(vals)
        min_v = np.min(vals)
        win_rate = (sum(1 for v in vals if v > 0) / len(vals)) * 100
        return f"{mean_v:+.2f}%", f"{med_v:+.2f}%", f"{max_v:+.2f}%", f"{min_v:+.2f}%", f"{win_rate:.1f}%"

    stat_rows = [
        ("Среднее (Mean)", 0),
        ("Медиана (Median)", 1),
        ("Максимум (Max)", 2),
        ("Минимум (Min)", 3),
        ("% Положительных (Win Rate)", 4)
    ]

    stat_start = len(matrix) + 4
    for s_idx, (stat_name, stat_pos) in enumerate(stat_rows, start=stat_start):
        s_row = [stat_name, "", "", "", "", "", "", ""]
        for d_key in ["R-1", "R", "R+1", "R+2"]:
            stats_tuple = calc_stats(day_values[d_key])
            s_row.append(stats_tuple[stat_pos])
        ws.append(s_row)
        ws.merge_cells(start_row=s_idx, start_column=1, end_row=s_idx, end_column=8)
        c_label = ws.cell(row=s_idx, column=1)
        c_label.font = Font(name="Calibri", size=9.5, bold=True, color="475569")
        c_label.alignment = Alignment(horizontal="right", vertical="center")
        c_label.fill = PatternFill(start_color="F1F5F9", end_color="F1F5F9", fill_type="solid")
        for c_i in range(1, 13):
            ws.cell(row=s_idx, column=c_i).border = cell_border
            if c_i >= 9:
                ws.cell(row=s_idx, column=c_i).alignment = Alignment(horizontal="center", vertical="center")
                ws.cell(row=s_idx, column=c_i).font = Font(name="Calibri", size=9.5, bold=True)

    # Column dimensions
    for col in ws.columns:
        max_len = max(len(str(cell.value or '')) for cell in col)
        col_letter = get_column_letter(col[0].column)
        ws.column_dimensions[col_letter].width = max(max_len + 3, 13)

    output = io.BytesIO()
    wb.save(output)
    output.seek(0)
    filename = f"{ticker}_Earnings_Moves_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
    return Response(
        content=output.getvalue(),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'}
    )


# ============================================================
# QUANT CORRELATIONS & BETA MATRIX
# ============================================================

@app.get("/api/ticker/{ticker}/correlations")
async def api_correlations(
    ticker: str,
    period: str = Query("1y", pattern="^(2w|1m|3m|6m|ytd|1y|earnings_8q)$"),
    peers: Optional[str] = None,
    benchmarks: Optional[str] = None
):
    """
    Calculates Pearson correlation and Beta coefficients against SPY, QQQ, SOXX, IGV, DRAM, and stock peers.
    Supports period="earnings_8q" for calculations restricted to earnings dates.
    """
    ticker = ticker.strip().upper()
    peer_list = [p.strip().upper() for p in peers.split(",") if p.strip()] if peers is not None else None
    bm_list = [b.strip().upper() for b in benchmarks.split(",") if b.strip()] if benchmarks is not None else None
    result = quant_client.calculate_ticker_correlations(
        ticker=ticker,
        period=period,
        peers=peer_list,
        benchmarks=bm_list
    )
    return result


@app.post("/api/ticker/{ticker}/correlations/export")
async def export_correlations_matrix(ticker: str, request: Request):
    """
    Exports the Correlation and Beta Matrix table to a styled Excel spreadsheet (.xlsx).
    """
    ticker = ticker.strip().upper()
    data = await request.json() or {}
    rows = data.get("rows", [])
    period = str(data.get("period", "1y"))
    target_move = data.get("target_move", 2.0)

    if not rows:
        raise HTTPException(status_code=400, detail="Нет данных для экспорта.")

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = f"{ticker} Correlations"

    # Title row
    ws.merge_cells("A1:K1")
    title_cell = ws["A1"]
    title_cell.value = f"CHEPUH / FinWhale • Матрица корреляций и коэффициентов Beta: {ticker} (Горизонт: {period})"
    title_cell.font = Font(name="Calibri", size=12, bold=True, color="1E293B")
    title_cell.alignment = Alignment(horizontal="left", vertical="center")
    ws.row_dimensions[1].height = 26

    header_fill = PatternFill(start_color="1E293B", end_color="1E293B", fill_type="solid")
    header_font = Font(name="Calibri", size=10, bold=True, color="FFFFFF")
    header_alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    border_thin = Side(border_style="thin", color="CBD5E1")
    cell_border = Border(left=border_thin, right=border_thin, top=border_thin, bottom=border_thin)

    pos_fill = PatternFill(start_color="E8F5E9", end_color="E8F5E9", fill_type="solid")
    pos_font = Font(name="Calibri", size=10, color="1B5E20", bold=True)
    neg_fill = PatternFill(start_color="FFEBEE", end_color="FFEBEE", fill_type="solid")
    neg_font = Font(name="Calibri", size=10, color="B71C1C", bold=True)
    regular_font = Font(name="Calibri", size=10, color="000000")
    bold_font = Font(name="Calibri", size=10, bold=True, color="000000")

    headers = [
        "Тикер",
        "Тип",
        "Корреляция",
        "Beta",
        "Ход аналога (Move %)",
        f"Где должен стоять {ticker} (%)",
        f"Ход {ticker} (%)",
        "Разница (Diff %)",
        "Normalized (σ)",
        "Наблюдений (дн.)",
        "Статус истории"
    ]

    ws.append(headers)
    ws.row_dimensions[2].height = 24

    for col_idx in range(1, len(headers) + 1):
        c = ws.cell(row=2, column=col_idx)
        c.fill = header_fill
        c.font = header_font
        c.alignment = header_alignment
        c.border = cell_border

    # Beta fills matching Photo 1 palette
    beta_neg_fill = PatternFill(start_color="FECACA", end_color="FECACA", fill_type="solid")
    beta_neg_font = Font(name="Calibri", size=10, color="991B1B", bold=True)
    beta_low_fill = PatternFill(start_color="FEF3C7", end_color="FEF3C7", fill_type="solid")
    beta_low_font = Font(name="Calibri", size=10, color="78350F", bold=False)
    beta_neutral_fill = PatternFill(start_color="BFDBFE", end_color="BFDBFE", fill_type="solid")
    beta_neutral_font = Font(name="Calibri", size=10, color="1E3A8A", bold=True)
    beta_high_fill = PatternFill(start_color="BBF7D0", end_color="BBF7D0", fill_type="solid")
    beta_high_font = Font(name="Calibri", size=10, color="14532D", bold=True)

    # Correlation fills matching light-to-grey gradient
    corr_high_fill = PatternFill(start_color="DCFCE7", end_color="DCFCE7", fill_type="solid")
    corr_high_font = Font(name="Calibri", size=10, color="166534", bold=True)
    corr_med_fill = PatternFill(start_color="E8F5E9", end_color="E8F5E9", fill_type="solid")
    corr_med_font = Font(name="Calibri", size=10, color="2E7D32")
    corr_slate_fill = PatternFill(start_color="F1F5F9", end_color="F1F5F9", fill_type="solid")
    corr_slate_font = Font(name="Calibri", size=10, color="475569")
    corr_grey_fill = PatternFill(start_color="E2E8F0", end_color="E2E8F0", fill_type="solid")
    corr_grey_font = Font(name="Calibri", size=10, color="64748B")

    current_row = 3
    for r in rows:
        sym = str(r.get("symbol", "")).strip().upper()
        t_type = str(r.get("type", "Peer"))
        corr = r.get("correlation")
        beta = r.get("beta")
        peer_move = r.get("_peerMove", r.get("peer_move", 1.0))
        exp_move = r.get("_expMove", r.get("expected_move"))
        t_move = r.get("_targetMove", r.get("target_move", target_move))
        diff = r.get("_diff", r.get("diff"))
        norm_diff = r.get("_normDiff", r.get("normalized"))
        obs = r.get("observations")
        is_partial = bool(r.get("is_partial", False))
        part_reason = r.get("partial_reason") or ("Меньше горизонта" if is_partial else "Полная история")

        ws.append([
            sym,
            t_type,
            round(float(corr), 4) if (corr is not None and not (isinstance(corr, float) and np.isnan(corr))) else None,
            round(float(beta), 4) if (beta is not None and not (isinstance(beta, float) and np.isnan(beta))) else None,
            round(float(peer_move), 2) if (peer_move is not None and not (isinstance(peer_move, float) and np.isnan(peer_move))) else None,
            round(float(exp_move), 2) if (exp_move is not None and not (isinstance(exp_move, float) and np.isnan(exp_move))) else None,
            round(float(t_move), 2) if (t_move is not None and not (isinstance(t_move, float) and np.isnan(t_move))) else None,
            round(float(diff), 2) if (diff is not None and not (isinstance(diff, float) and np.isnan(diff))) else None,
            round(float(norm_diff), 2) if (norm_diff is not None and not (isinstance(norm_diff, float) and np.isnan(norm_diff))) else None,
            int(obs) if (obs is not None and not (isinstance(obs, float) and np.isnan(obs))) else None,
            part_reason
        ])

        ws.row_dimensions[current_row].height = 20

        # Style cells
        for c_idx in range(1, len(headers) + 1):
            cell = ws.cell(row=current_row, column=c_idx)
            cell.border = cell_border
            cell.font = regular_font
            cell.alignment = Alignment(horizontal="center", vertical="center")

        # Symbol bold
        ws.cell(row=current_row, column=1).font = bold_font

        # Correlation formatting with gradient-fill (higher = lighter, lower = grey)
        c_cell = ws.cell(row=current_row, column=3)
        if corr is not None and not (isinstance(corr, float) and np.isnan(corr)):
            c_val = float(corr)
            c_cell.number_format = "+0.000;-0.000;0.000"
            if c_val >= 0.70:
                c_cell.fill = corr_high_fill
                c_cell.font = corr_high_font
            elif c_val >= 0.40:
                c_cell.fill = corr_med_fill
                c_cell.font = corr_med_font
            elif c_val >= 0.10:
                c_cell.fill = corr_slate_fill
                c_cell.font = corr_slate_font
            elif c_val >= -0.10:
                c_cell.fill = corr_grey_fill
                c_cell.font = corr_grey_font
            else:
                c_cell.fill = neg_fill
                c_cell.font = neg_font

        # Beta formatting with conditional fill matching Photo 1
        b_cell = ws.cell(row=current_row, column=4)
        if beta is not None and not (isinstance(beta, float) and np.isnan(beta)):
            b_val = float(beta)
            b_cell.number_format = "0.000"
            if b_val < 0:
                b_cell.fill = beta_neg_fill
                b_cell.font = beta_neg_font
            elif b_val < 0.85:
                b_cell.fill = beta_low_fill
                b_cell.font = beta_low_font
            elif b_val <= 1.15:
                b_cell.fill = beta_neutral_fill
                b_cell.font = beta_neutral_font
            else:
                b_cell.fill = beta_high_fill
                b_cell.font = beta_high_font

        # Diff formatting with fill
        d_cell = ws.cell(row=current_row, column=8)
        if diff is not None and not (isinstance(diff, float) and np.isnan(diff)):
            d_val = float(diff)
            d_cell.number_format = "+0.00;-0.00;0.00"
            if d_val > 0.001:
                d_cell.fill = pos_fill
                d_cell.font = pos_font
            elif d_val < -0.001:
                d_cell.fill = neg_fill
                d_cell.font = neg_font

        # Partial row warning in Excel
        if is_partial:
            part_cell = ws.cell(row=current_row, column=11)
            part_cell.font = Font(name="Calibri", size=9, color="B45309", bold=True)
            part_cell.fill = PatternFill(start_color="FEF3C7", end_color="FEF3C7", fill_type="solid")

        current_row += 1

    # Auto column width
    for col in ws.columns:
        max_len = 0
        col_letter = get_column_letter(col[0].column)
        for cell in col:
            val_str = str(cell.value or "")
            if cell.row == 1:
                continue
            max_len = max(max_len, len(val_str))
        ws.column_dimensions[col_letter].width = max(max_len + 4, 12)

    output = io.BytesIO()
    wb.save(output)
    output.seek(0)
    filename = f"{ticker}_correlation_matrix_{period}.xlsx"
    headers_resp = {"Content-Disposition": f'attachment; filename="{filename}"'}
    return Response(
        content=output.getvalue(),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers=headers_resp
    )


@app.post("/api/matrix/export")
async def export_general_matrix(request: Request):
    """
    Exports Matrix Correlation Pearson or Matrix Beta (formul Slope) to styled Excel (.xlsx).
    """
    data = await request.json() or {}
    m_type = str(data.get("type", "correlation")).lower()
    title = str(data.get("title", "Matrix Export"))
    period = str(data.get("period", "1y"))
    symbols = data.get("symbols", [])
    matrix = data.get("matrix", {})

    if not symbols:
        raise HTTPException(status_code=400, detail="Нет данных для экспорта.")

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Beta Matrix" if m_type == "beta" else "Correlation Matrix"

    # Title row
    last_col_letter = get_column_letter(len(symbols) + 1)
    ws.merge_cells(f"A1:{last_col_letter}1")
    title_cell = ws["A1"]
    title_cell.value = f"CHEPUH / FinWhale • {title} (Горизонт: {period})"
    title_cell.font = Font(name="Calibri", size=12, bold=True, color="1E293B")
    title_cell.alignment = Alignment(horizontal="left", vertical="center")
    ws.row_dimensions[1].height = 26

    # Headers
    header_fill = PatternFill(start_color="1E293B", end_color="1E293B", fill_type="solid")
    header_font = Font(name="Calibri", size=10, bold=True, color="FFFFFF")
    header_alignment = Alignment(horizontal="center", vertical="center")
    border_thin = Side(border_style="thin", color="CBD5E1")
    cell_border = Border(left=border_thin, right=border_thin, top=border_thin, bottom=border_thin)

    headers = ["Тикер"] + [str(s).upper() for s in symbols]
    ws.append(headers)
    ws.row_dimensions[2].height = 24

    for col_idx in range(1, len(headers) + 1):
        c = ws.cell(row=2, column=col_idx)
        c.fill = header_fill
        c.font = header_font
        c.alignment = header_alignment
        c.border = cell_border

    # Palette definitions
    # Beta palette (matching user photo)
    beta_neg_fill = PatternFill(start_color="FECACA", end_color="FECACA", fill_type="solid")
    beta_neg_font = Font(name="Calibri", size=10, color="991B1B", bold=True)
    beta_low_fill = PatternFill(start_color="FEF3C7", end_color="FEF3C7", fill_type="solid")
    beta_low_font = Font(name="Calibri", size=10, color="78350F", bold=False)
    beta_neutral_fill = PatternFill(start_color="BFDBFE", end_color="BFDBFE", fill_type="solid")
    beta_neutral_font = Font(name="Calibri", size=10, color="1E3A8A", bold=True)
    beta_high_fill = PatternFill(start_color="BBF7D0", end_color="BBF7D0", fill_type="solid")
    beta_high_font = Font(name="Calibri", size=10, color="14532D", bold=True)

    # Correlation palette (lighter for high, grey for low)
    corr_high_fill = PatternFill(start_color="DCFCE7", end_color="DCFCE7", fill_type="solid")
    corr_high_font = Font(name="Calibri", size=10, color="166534", bold=True)
    corr_med_fill = PatternFill(start_color="E8F5E9", end_color="E8F5E9", fill_type="solid")
    corr_med_font = Font(name="Calibri", size=10, color="2E7D32")
    corr_slate_fill = PatternFill(start_color="F1F5F9", end_color="F1F5F9", fill_type="solid")
    corr_slate_font = Font(name="Calibri", size=10, color="475569")
    corr_grey_fill = PatternFill(start_color="E2E8F0", end_color="E2E8F0", fill_type="solid")
    corr_grey_font = Font(name="Calibri", size=10, color="64748B")
    corr_neg_fill = PatternFill(start_color="FEE2E2", end_color="FEE2E2", fill_type="solid")
    corr_neg_font = Font(name="Calibri", size=10, color="991B1B", bold=True)

    regular_font = Font(name="Calibri", size=10, color="000000")
    ticker_font = Font(name="Calibri", size=10, bold=True, color="1E293B")

    current_row = 3
    for row_sym in symbols:
        row_vals = [row_sym]
        for col_sym in symbols:
            if row_sym == col_sym:
                diag_val = matrix.get(row_sym, {}).get(col_sym) if matrix else 1.0
                if diag_val is not None and not (isinstance(diag_val, float) and np.isnan(diag_val)):
                    row_vals.append(round(float(diag_val), 3))
                elif not matrix:
                    row_vals.append(1.0)
                else:
                    row_vals.append(None)
            else:
                v = matrix.get(row_sym, {}).get(col_sym)
                if v is not None and not (isinstance(v, float) and np.isnan(v)):
                    row_vals.append(round(float(v), 3))
                else:
                    row_vals.append(None)
        ws.append(row_vals)
        ws.row_dimensions[current_row].height = 20

        # Style row
        for c_idx in range(1, len(headers) + 1):
            cell = ws.cell(row=current_row, column=c_idx)
            cell.border = cell_border
            cell.alignment = Alignment(horizontal="center", vertical="center")

            if c_idx == 1:
                cell.font = ticker_font
                continue

            val = cell.value
            if val is None:
                cell.font = Font(name="Calibri", size=10, color="94A3B8")
                cell.value = "—"
                continue

            cell.number_format = "0.000" if m_type == "beta" else "+0.000;-0.000;0.000"

            if row_sym == symbols[c_idx - 2]:
                if m_type == "beta":
                    cell.fill = beta_neutral_fill
                    cell.font = beta_neutral_font
                else:
                    cell.fill = corr_high_fill
                    cell.font = corr_high_font
                continue

            num = float(val)
            if m_type == "beta":
                if num < 0:
                    cell.fill = beta_neg_fill
                    cell.font = beta_neg_font
                elif num < 0.85:
                    cell.fill = beta_low_fill
                    cell.font = beta_low_font
                elif num <= 1.15:
                    cell.fill = beta_neutral_fill
                    cell.font = beta_neutral_font
                else:
                    cell.fill = beta_high_fill
                    cell.font = beta_high_font
            else:
                if num >= 0.70:
                    cell.fill = corr_high_fill
                    cell.font = corr_high_font
                elif num >= 0.40:
                    cell.fill = corr_med_fill
                    cell.font = corr_med_font
                elif num >= 0.10:
                    cell.fill = corr_slate_fill
                    cell.font = corr_slate_font
                elif num >= -0.10:
                    cell.fill = corr_grey_fill
                    cell.font = corr_grey_font
                else:
                    cell.fill = corr_neg_fill
                    cell.font = corr_neg_font

        current_row += 1

    # Widths
    for col in ws.columns:
        max_len = 0
        col_letter = get_column_letter(col[0].column)
        for cell in col:
            if cell.row == 1:
                continue
            max_len = max(max_len, len(str(cell.value or "")))
        ws.column_dimensions[col_letter].width = max(max_len + 4, 11)

    output = io.BytesIO()
    wb.save(output)
    output.seek(0)
    clean_title = re.sub(r'[^a-zA-Z0-9_-]', '_', title).strip('_')
    filename = f"{clean_title}_{period}.xlsx"
    return Response(
        content=output.getvalue(),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'}
    )


# ============================================================
# DIVIDENDS HISTORY
# ============================================================

@app.get("/api/ticker/{ticker}/dividends")
async def api_dividends(ticker: str):
    ticker = ticker.strip().upper()
    db_divs = get_ticker_dividends_db(ticker)
    if not db_divs:
        # 1. Try FMP
        fmp_divs = fmp_client.get_dividends_history(ticker, limit=24)
        if not fmp_divs:
            # 2. Try Polygon fallback
            fmp_divs = polygon_client.fetch_dividends(ticker, limit=24)
        if fmp_divs:
            save_ticker_dividends_db(ticker, fmp_divs)
            db_divs = fmp_divs

    return {
        "ticker": ticker,
        "count": len(db_divs),
        "dividends": db_divs
    }


# ============================================================
# ON-DEMAND SYNC
# ============================================================

@app.post("/api/ticker/{ticker}/sync")
async def api_sync_ticker(ticker: str):
    ticker = ticker.strip().upper()
    # 1. Sync Profile & Peers
    prof = fmp_client.get_company_profile(ticker)
    peers = fmp_client.get_stock_peers(ticker)
    prof["peers"] = [p["symbol"] for p in peers if p.get("symbol")]
    save_company_profile_db(ticker, prof)

    # 2. Sync Dividends
    divs = fmp_client.get_dividends_history(ticker, limit=24)
    if divs:
        save_ticker_dividends_db(ticker, divs)

    # 3. Fetch latest Daily bars
    daily_bars = polygon_client.fetch_daily_bars(ticker, start_date="2023-01-01")

    return {
        "ticker": ticker,
        "synced": True,
        "daily_bars": len(daily_bars),
        "dividends": len(divs),
        "peers": prof["peers"]
    }


# ============================================================
# DATALENS ENDPOINTS
# ============================================================

@app.get("/api/datalens/data")
async def api_datalens_data(
    tickers: str = Query("SPY,QQQ,MU"),
    period: str = Query("1y"),
    start_date: Optional[str] = Query(None),
    end_date: Optional[str] = Query(None)
):
    """
    Returns daily percentage changes (cls2cls, intra, gap) and event markers
    (Earnings, Ex-Div, Pay Date) for the requested tickers over the chosen period.
    """
    try:
        if not isinstance(start_date, str):
            start_date = None
        if not isinstance(end_date, str):
            end_date = None
        if not isinstance(tickers, str):
            tickers = "SPY,QQQ,MU"
        if not isinstance(period, str):
            period = "1y"

        raw_list = [t.strip().upper() for t in re.split(r"[\s,;]+", tickers) if t.strip()]
        clean_tickers = list(dict.fromkeys(raw_list))
        if not clean_tickers:
            clean_tickers = ["SPY", "QQQ", "MU"]

        df = get_stock_data(*clean_tickers)
        if df is None or df.empty:
            return {
                "status": "ok",
                "as_of": datetime.date.today().isoformat(),
                "tickers": clean_tickers,
                "dates": [],
                "data": {}
            }

        df["date_str"] = df["date"].dt.strftime("%Y-%m-%d")

        # Calculate Intra% = (close - open) / open * 100
        df["intra"] = np.where(df["open"] > 0, (df["close"] - df["open"]) / df["open"] * 100.0, 0.0)

        # Date filtering
        all_dates = sorted(df["date_str"].unique())
        if not all_dates:
            return {"status": "ok", "as_of": "", "tickers": clean_tickers, "dates": [], "data": {}}

        latest_date_str = all_dates[-1]
        latest_date = pd.to_datetime(latest_date_str)

        # Build ticker trading dates mapping (unsliced for boundary lookups)
        ticker_trading_dates = {}
        for sym in clean_tickers:
            sym_df = df[df["ticker"] == sym]
            if not sym_df.empty:
                ticker_trading_dates[sym] = sorted(sym_df["date_str"].unique())

        if start_date and end_date:
            df = df[(df["date_str"] >= start_date) & (df["date_str"] <= end_date)]
        elif start_date:
            df = df[df["date_str"] >= start_date]
        else:
            p = period.lower().strip()
            if p == "1w":
                cutoff = latest_date - pd.Timedelta(days=7)
            elif p == "1m":
                cutoff = latest_date - pd.Timedelta(days=30)
            elif p == "3m":
                cutoff = latest_date - pd.Timedelta(days=91)
            elif p == "6m":
                cutoff = latest_date - pd.Timedelta(days=182)
            elif p == "ytd":
                cutoff = pd.Timestamp(year=latest_date.year, month=1, day=1)
            elif p == "1y":
                cutoff = latest_date - pd.Timedelta(days=365)
            elif p == "2y":
                cutoff = latest_date - pd.Timedelta(days=730)
            elif p == "5y":
                cutoff = latest_date - pd.Timedelta(days=1825)
            elif p == "max":
                cutoff = None
            else:
                cutoff = latest_date - pd.Timedelta(days=365)

            if cutoff is not None:
                cutoff_str = cutoff.strftime("%Y-%m-%d")
                df = df[df["date_str"] >= cutoff_str]

        # Fetch earnings events with R-1, R, R+1, R+2 offsets
        earnings_offsets_map = {}
        earnings_r_set = set()
        try:
            reports_rows = []
            if check_postgres_health():
                conn = None
                try:
                    conn = get_postgres_connection()
                    with conn.cursor() as cur:
                        cur.execute("""
                            SELECT UPPER(t.ticker), r.date::text, r.move_date::text, r.time::text
                            FROM reports r
                            JOIN ticker t ON t.id = r.id_ticker
                            WHERE UPPER(t.ticker) = ANY(%s)
                            ORDER BY r.date ASC;
                        """, (clean_tickers,))
                        reports_rows = list(cur.fetchall())
                except Exception as ex_pg:
                    print(f"[DataLens] PostgreSQL reports fetch error: {ex_pg}")
                finally:
                    if conn:
                        conn.close()

            # Merge reports from SQLite reports_extra cache (for tickers like SPCX)
            if os.path.exists(CACHE_DB_PATH):
                try:
                    with sqlite3.connect(CACHE_DB_PATH) as s_conn:
                        s_cur = s_conn.cursor()
                        q_marks = ",".join(["?"] * len(clean_tickers))
                        s_cur.execute(f"SELECT UPPER(ticker), date, date as move_date, time FROM reports_extra WHERE UPPER(ticker) IN ({q_marks})", clean_tickers)
                        existing_keys = {(r[0], r[1]) for r in reports_rows}
                        for r_row in s_cur.fetchall():
                            if (r_row[0], r_row[1]) not in existing_keys:
                                reports_rows.append(r_row)
                except Exception as ex_sq:
                    print(f"[DataLens] SQLite reports_extra fetch error: {ex_sq}")

            for row in reports_rows:
                sym, r_d, m_d, r_time = row
                dates = ticker_trading_dates.get(sym, [])
                if not dates:
                    continue

                time_str = str(r_time or "").lower()
                is_amc = any(x in time_str for x in ["16:", "17:", "18:", "19:", "20:", "pm", "amc", "after"])

                # Determine Reaction date R
                if m_d and m_d != r_d and m_d in dates:
                    R = m_d
                elif r_d in dates:
                    r_d_idx = dates.index(r_d)
                    if is_amc and r_d_idx + 1 < len(dates):
                        R = dates[r_d_idx + 1]
                    else:
                        R = r_d
                else:
                    R = m_d or r_d

                if R in dates:
                    r_idx = dates.index(R)
                    earnings_offsets_map.setdefault((sym, dates[r_idx]), set()).add("R")
                    earnings_r_set.add((sym, dates[r_idx]))
                    if r_idx > 0:
                        earnings_offsets_map.setdefault((sym, dates[r_idx - 1]), set()).add("R-1")
                    if r_idx + 1 < len(dates):
                        earnings_offsets_map.setdefault((sym, dates[r_idx + 1]), set()).add("R+1")
                    if r_idx + 2 < len(dates):
                        earnings_offsets_map.setdefault((sym, dates[r_idx + 2]), set()).add("R+2")

                # R+W: Next calendar week (Mon-Sun) after the company's earnings report date
                rep_date_str = r_d or m_d
                if rep_date_str:
                    try:
                        dt_parts = [int(p) for p in str(rep_date_str).split("-")[:3]]
                        rep_dt = datetime.date(dt_parts[0], dt_parts[1], dt_parts[2])
                        cur_mon = rep_dt - datetime.timedelta(days=rep_dt.weekday())
                        next_mon = cur_mon + datetime.timedelta(days=7)
                        next_sun = cur_mon + datetime.timedelta(days=13)
                        next_mon_str = next_mon.strftime("%Y-%m-%d")
                        next_sun_str = next_sun.strftime("%Y-%m-%d")
                        for d_str in dates:
                            if next_mon_str <= d_str <= next_sun_str:
                                earnings_offsets_map.setdefault((sym, d_str), set()).add("R+W")
                    except Exception as ex_w:
                        print(f"[DataLens] Error computing R+W for {sym} {rep_date_str}: {ex_w}")
        except Exception as e:
            print("[DataLens] Earnings fetch error:", e)

        # Fetch dividend events (ex-date, pay-date)
        ex_div_set = set()
        pay_date_set = set()
        try:
            if os.path.exists(CACHE_DB_PATH):
                with sqlite3.connect(CACHE_DB_PATH) as s_conn:
                    s_cur = s_conn.cursor()
                    q_marks = ",".join(["?"] * len(clean_tickers))
                    s_cur.execute(f"SELECT UPPER(ticker), ex_date, pay_date FROM dividends WHERE UPPER(ticker) IN ({q_marks})", clean_tickers)
                    for row in s_cur.fetchall():
                        t_sym, ex_d, pay_d = row
                        if ex_d: ex_div_set.add((t_sym, ex_d))
                        if pay_d: pay_date_set.add((t_sym, pay_d))
        except Exception as e:
            print("[DataLens] Dividends fetch error:", e)

        # Fetch 08:00 - 09:35 auction and OPG data from PostgreSQL intraday_chart
        auction_map = {}
        ticker_exchanges = {}
        bounds_map = {}
        try:
            if clean_tickers and not df.empty:
                min_date_str = df["date_str"].min()
                max_date_str = df["date_str"].max()
                conn = get_postgres_connection()
                try:
                    cur = conn.cursor()
                    # Fetch exchanges for clean_tickers
                    cur.execute("""
                        SELECT UPPER(t.ticker), e.mic, e.operating_mic, e.name
                        FROM ticker t
                        LEFT JOIN exchange e ON e.id = t.id_exchange
                        WHERE UPPER(t.ticker) = ANY(%s);
                    """, (clean_tickers,))
                    for row in cur.fetchall():
                        sym_u, mic, op_mic, ex_name = row
                        mic_str = (op_mic or mic or ex_name or "").upper()
                        ticker_exchanges[sym_u] = "NSDQ" if ("XNAS" in mic_str or "NASDAQ" in mic_str) else "NYSE"

                    # Fetch boundary bars (first trade of day = premarket start, last trade of day = postmarket end)
                    cur.execute("""
                        WITH day_bounds AS (
                            SELECT 
                                UPPER(t.ticker) as ticker,
                                i.date::text as date,
                                to_char(i.time, 'HH24:MI:SS') as time_str,
                                i.open::float as open,
                                i.close::float as close,
                                ROW_NUMBER() OVER(PARTITION BY i.id_ticker, i.date ORDER BY i.time ASC) as rn_first,
                                ROW_NUMBER() OVER(PARTITION BY i.id_ticker, i.date ORDER BY i.time DESC) as rn_last
                            FROM intraday_chart i
                            JOIN ticker t ON t.id = i.id_ticker
                            WHERE UPPER(t.ticker) = ANY(%s)
                              AND i.date >= %s AND i.date <= %s
                        )
                        SELECT ticker, date, time_str, open, close, 
                               CASE WHEN rn_first = 1 THEN 'FIRST' ELSE 'LAST' END as bound_type
                        FROM day_bounds
                        WHERE rn_first = 1 OR rn_last = 1;
                    """, (clean_tickers, min_date_str, max_date_str))
                    for b_row in cur.fetchall():
                        t_sym, d_val, t_str, o_val, c_val, b_type = b_row
                        if b_type == "FIRST":
                            bounds_map.setdefault((t_sym, d_val), {})["first"] = (t_str, o_val)
                        else:
                            bounds_map.setdefault((t_sym, d_val), {})["last"] = (t_str, c_val)

                    # Fetch intraday minute bars from 08:00 to 09:35
                    cur.execute("""
                        SELECT 
                            UPPER(t.ticker) as ticker,
                            i.date::text as date,
                            to_char(i.time, 'HH24:MI:SS') as time_str,
                            i.open::float as open,
                            i.close::float as close
                        FROM intraday_chart i
                        JOIN ticker t ON t.id = i.id_ticker
                        WHERE UPPER(t.ticker) = ANY(%s)
                          AND i.date >= %s AND i.date <= %s
                          AND i.time >= '08:00:00' AND i.time <= '09:35:00'
                        ORDER BY i.date ASC, i.time ASC;
                    """, (clean_tickers, min_date_str, max_date_str))
                    intraday_rows = cur.fetchall()

                    bars_by_td = {}
                    for row in intraday_rows:
                        t_sym, d_val, time_str, o_val, c_val = row
                        bars_by_td.setdefault((t_sym, d_val), []).append((time_str, o_val, c_val))

                    for (t_sym, d_val), bar_list in bars_by_td.items():
                        # Market Open price at 09:30:00
                        bars_at_930 = [b for b in bar_list if b[0] == "09:30:00"]
                        if bars_at_930:
                            open_930 = bars_at_930[0][1]
                        else:
                            bars_before_930 = [b for b in bar_list if b[0] <= "09:30:00"]
                            open_930 = bars_before_930[-1][2] if bars_before_930 else None

                        # Price at 09:35:00
                        bars_at_935 = [b for b in bar_list if b[0] == "09:35:00"]
                        if bars_at_935:
                            price_935 = bars_at_935[0][2]
                        else:
                            bars_before_935 = [b for b in bar_list if b[0] <= "09:35:00"]
                            price_935 = bars_before_935[-1][2] if bars_before_935 else None

                        # 1. OPG % (09:30 -> 09:35)
                        opg_pct = round(((price_935 - open_930) / open_930) * 100.0, 2) if (open_930 and price_935 and open_930 > 0) else None

                        # 2. Auction NSDQ (09:25 -> 09:30)
                        bars_nsdq = [b for b in bar_list if b[0] >= "09:25:00" and b[0] < "09:30:00"]
                        open_nsdq = bars_nsdq[0][1] if bars_nsdq else None
                        auc_nsdq_pct = round(((open_930 - open_nsdq) / open_nsdq) * 100.0, 2) if (open_930 and open_nsdq and open_nsdq > 0) else None

                        # 3. Auction NYSE (08:00 -> 09:30)
                        bars_nyse = [b for b in bar_list if b[0] >= "08:00:00" and b[0] < "09:30:00"]
                        open_nyse = bars_nyse[0][1] if bars_nyse else None
                        auc_nyse_pct = round(((open_930 - open_nyse) / open_nyse) * 100.0, 2) if (open_930 and open_nyse and open_nyse > 0) else None

                        # Default auction according to ticker exchange
                        default_ex = ticker_exchanges.get(t_sym, "NSDQ")
                        if default_ex == "NSDQ":
                            def_auc = auc_nsdq_pct if auc_nsdq_pct is not None else auc_nyse_pct
                            def_open = open_nsdq if open_nsdq is not None else open_nyse
                        else:
                            def_auc = auc_nyse_pct if auc_nyse_pct is not None else auc_nsdq_pct
                            def_open = open_nyse if open_nyse is not None else open_nsdq

                        auction_map[(t_sym, d_val)] = {
                            "opg": opg_pct,
                            "opg_open": round(float(open_930), 2) if open_930 else None,
                            "opg_price_935": round(float(price_935), 2) if price_935 else None,
                            "auction_nsdq": auc_nsdq_pct,
                            "auction_nsdq_start": round(float(open_nsdq), 2) if open_nsdq else None,
                            "auction_nsdq_end": round(float(open_930), 2) if open_930 else None,
                            "auction_nyse": auc_nyse_pct,
                            "auction_nyse_start": round(float(open_nyse), 2) if open_nyse else None,
                            "auction_nyse_end": round(float(open_930), 2) if open_930 else None,
                            "auction": def_auc,
                            "auction_open": round(float(def_open), 2) if def_open else None,
                            "auction_price_930": round(float(open_930), 2) if open_930 else None,
                            "exchange": default_ex
                        }
                finally:
                    conn.close()
        except Exception as e:
            print("[DataLens] OPG/Auction/Session fetch error:", e)

        # Build data dictionary by date
        data_by_date = {}
        for _, r in df.iterrows():
            d_str = r["date_str"]
            sym = r["ticker"]
            if d_str not in data_by_date:
                data_by_date[d_str] = {}

            cls2cls_val = round(float(r["cls2cls"]), 2) if pd.notnull(r["cls2cls"]) else None
            intra_val = round(float(r["intra"]), 2) if pd.notnull(r["intra"]) else None
            gap_val = round(float(r["gap"]), 2) if pd.notnull(r["gap"]) else None
            open_val = float(r["open"]) if pd.notnull(r["open"]) else None
            close_val = float(r["close"]) if pd.notnull(r["close"]) else None
            prev_close_val = round(float(r["prev_close"]), 2) if pd.notnull(r["prev_close"]) else None
            auc_info = auction_map.get((sym, d_str), {})
            b_info = bounds_map.get((sym, d_str), {})

            # Sessions:
            # 1. Intraday session (09:30 -> 16:00)
            sess_intra = intra_val
            sess_intra_start = round(open_val, 2) if open_val else None
            sess_intra_end = round(close_val, 2) if close_val else None

            # 2. Premarket session (04:00 -> 09:30)
            first_b = b_info.get("first")
            if first_b and first_b[0] < "09:30:00" and open_val and open_val > 0:
                pre_open_p = first_b[1]
                sess_pre = round(((open_val - pre_open_p) / pre_open_p) * 100.0, 2) if pre_open_p > 0 else None
                sess_pre_start = round(float(pre_open_p), 2)
                sess_pre_end = round(float(open_val), 2)
                sess_pre_time = first_b[0][:5]
            else:
                sess_pre = None
                sess_pre_start = None
                sess_pre_end = None
                sess_pre_time = None

            # 3. Postmarket session (16:00 -> 20:00)
            last_b = b_info.get("last")
            if last_b and last_b[0] > "16:00:00" and close_val and close_val > 0:
                post_close_p = last_b[1]
                sess_post = round(((post_close_p - close_val) / close_val) * 100.0, 2)
                sess_post_start = round(float(close_val), 2)
                sess_post_end = round(float(post_close_p), 2)
                sess_post_time = last_b[0][:5]
            else:
                sess_post = None
                sess_post_start = None
                sess_post_end = None
                sess_post_time = None

            data_by_date[d_str][sym] = {
                "open": open_val,
                "close": close_val,
                "prev_close": prev_close_val,
                "cls2cls": cls2cls_val,
                "intra": intra_val,
                "gap": gap_val,
                "opg": auc_info.get("opg"),
                "opg_open": auc_info.get("opg_open"),
                "opg_price_935": auc_info.get("opg_price_935"),
                "auction": auc_info.get("auction"),
                "auction_nsdq": auc_info.get("auction_nsdq"),
                "auction_nsdq_start": auc_info.get("auction_nsdq_start"),
                "auction_nsdq_end": auc_info.get("auction_nsdq_end"),
                "auction_nyse": auc_info.get("auction_nyse"),
                "auction_nyse_start": auc_info.get("auction_nyse_start"),
                "auction_nyse_end": auc_info.get("auction_nyse_end"),
                "auction_open": auc_info.get("auction_open"),
                "auction_price_930": auc_info.get("auction_price_930"),
                "exchange": auc_info.get("exchange") or ticker_exchanges.get(sym, "NSDQ"),
                "session": sess_intra,
                "session_intra": sess_intra,
                "session_intra_start": sess_intra_start,
                "session_intra_end": sess_intra_end,
                "session_pre": sess_pre,
                "session_pre_start": sess_pre_start,
                "session_pre_end": sess_pre_end,
                "session_pre_time": sess_pre_time,
                "session_post": sess_post,
                "session_post_start": sess_post_start,
                "session_post_end": sess_post_end,
                "session_post_time": sess_post_time,
                "is_earnings": (sym, d_str) in earnings_r_set,
                "earnings_offsets": sorted(list(earnings_offsets_map.get((sym, d_str), set()))),
                "is_ex_div": (sym, d_str) in ex_div_set,
                "is_pay_date": (sym, d_str) in pay_date_set
            }

        dates_sorted = sorted(list(data_by_date.keys()), reverse=True)

        return {
            "status": "ok",
            "as_of": latest_date_str,
            "tickers": clean_tickers,
            "dates": dates_sorted,
            "data": data_by_date,
            "ticker_exchanges": ticker_exchanges
        }
    except Exception as e:
        print("[DataLens] api_datalens_data error:", e)
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/datalens/filter/hold-move")
async def api_datalens_hold_move_filter(
    ticker: str = Query("MU"),
    period: str = Query("1y"),
    start_date: Optional[str] = Query(None),
    end_date: Optional[str] = Query(None),
    threshold_pct: float = Query(1.0, ge=0.01, le=50.0),
    min_hold_pct: float = Query(0.90, ge=0.0, le=50.0),
    direction: str = Query("both"),
    window: str = Query("any"),
    hold_until: str = Query("09:30")
):
    """
    Calculates and returns trading dates where the specified ticker achieved at least
    `threshold_pct` move from prev_close during `window`, and held at least `min_hold_pct`
    until `hold_until` (09:30 OPG or 16:30).
    """
    try:
        matching_dates = calculate_hold_move_dates(
            ticker=ticker,
            period=period,
            start_date=start_date,
            end_date=end_date,
            threshold_pct=threshold_pct,
            min_hold_pct=min_hold_pct,
            direction=direction,
            window=window,
            hold_until=hold_until
        )
        return {
            "status": "ok",
            "ticker": ticker.upper().strip(),
            "matching_dates": matching_dates,
            "count": len(matching_dates),
            "params": {
                "period": period,
                "start_date": start_date,
                "end_date": end_date,
                "threshold_pct": threshold_pct,
                "min_hold_pct": min_hold_pct,
                "direction": direction,
                "window": window,
                "hold_until": hold_until
            }
        }
    except Exception as e:
        print(f"[DataLens] api_datalens_hold_move_filter error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ============================================================
# TIER POSITION SIZING (GARMAN-KLASS VOLATILITY PARITY)
# ============================================================

class TierCalculateRequest(BaseModel):
    benchmark: str = ""
    benchmark_shares: float = 5.0
    lookback_days: int = 20
    tickers: List[str] = []
    rounding: str = "1"

@app.get("/api/tier/calculate")
async def api_tier_calculate_get(
    benchmark: str = Query(""),
    benchmark_shares: float = Query(5.0),
    lookback_days: int = Query(20),
    tickers: Optional[str] = Query(None),
    rounding: str = Query("1")
):
    try:
        raw_list = [t.strip().upper() for t in re.split(r"[\s,;]+", tickers) if t.strip()] if tickers else []
        res = calculate_tier_position_sizing(
            benchmark=benchmark,
            benchmark_shares=benchmark_shares,
            lookback_days=lookback_days,
            tickers=raw_list,
            rounding_mode=rounding
        )
        return res
    except Exception as e:
        print(f"[Tier] api_tier_calculate_get error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/tier/calculate")
async def api_tier_calculate_post(payload: TierCalculateRequest):
    try:
        res = calculate_tier_position_sizing(
            benchmark=payload.benchmark,
            benchmark_shares=payload.benchmark_shares,
            lookback_days=payload.lookback_days,
            tickers=payload.tickers,
            rounding_mode=payload.rounding
        )
        return res
    except Exception as e:
        print(f"[Tier] api_tier_calculate_post error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/tier/export/excel")
async def api_tier_export_excel(payload: TierCalculateRequest):
    try:
        res = calculate_tier_position_sizing(
            benchmark=payload.benchmark,
            benchmark_shares=payload.benchmark_shares,
            lookback_days=payload.lookback_days,
            tickers=payload.tickers,
            rounding_mode=payload.rounding
        )
        excel_buf = build_tier_excel(res)
        filename = f"tier_sizing_{payload.benchmark}_{datetime.date.today().isoformat()}.xlsx"
        return Response(
            content=excel_buf.getvalue(),
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'}
        )
    except Exception as e:
        print(f"[Tier] api_tier_export_excel error: {e}")
        raise HTTPException(status_code=500, detail=str(e))



# CORRELATION & BETA CALCULATOR ENDPOINTS
# ============================================================

def clean_value(value):
    if value is None:
        return None
    if isinstance(value, (np.integer, int)):
        return int(value)
    if isinstance(value, (np.floating, float)):
        if np.isnan(value) or not np.isfinite(value):
            return None
        return float(value)
    return value


def clean_dict(data):
    if isinstance(data, dict):
        return {str(k): clean_dict(v) for k, v in data.items()}
    if isinstance(data, list):
        return [clean_dict(v) for v in data]
    return clean_value(data)


@app.post("/api/calculate")
async def api_calculate_single(request: Request):
    try:
        data = await request.json()
        if not data:
            raise HTTPException(status_code=400, detail="Пустой запрос.")

        stock = str(data.get("stock", "")).strip().upper()
        benchmark = str(data.get("benchmark", "")).strip().upper()
        benchmark_move = float(data.get("benchmark_move", 0.01))
        asset_move = data.get("asset_move")
        if asset_move is not None:
            asset_move = float(asset_move)

        if not stock or not benchmark:
            raise HTTPException(status_code=400, detail="Не указан тикер или benchmark.")
        if stock == benchmark:
            raise HTTPException(status_code=400, detail="Акции должны быть разными.")

        df = get_stock_data(stock, benchmark)
        if df is None or df.empty:
            raise HTTPException(status_code=404, detail="Данные не найдены.")

        results, _ = calculate_all_periods(
            df,
            stock,
            benchmark,
            benchmark_move=benchmark_move,
            asset_move=asset_move
        )

        return {
            "stock": stock,
            "benchmark": benchmark,
            "benchmark_move": benchmark_move,
            "asset_move": asset_move,
            "results": clean_dict(results),
            "chart": []
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/calculate-groups")
@app.post("/api/calculate_groups")
async def api_calculate_groups(request: Request):
    try:
        data = await request.json()
        if not data:
            raise HTTPException(status_code=400, detail="Пустой запрос.")

        groups = data.get("groups")
        benchmark_raw = data.get("benchmarks") or data.get("benchmark") or data.get("reference")
        period = data.get("period") or "1y"
        benchmark_move = float(data.get("benchmark_move", 0.01))
        benchmark_moves = data.get("benchmark_moves", {})
        asset_moves = data.get("asset_moves", {})

        if not groups or not isinstance(groups, dict):
            raise HTTPException(status_code=400, detail="Добавьте хотя бы одну группу с тикерами.")
        if not benchmark_raw:
            raise HTTPException(status_code=400, detail="Не указан Market Asset (Benchmark).")

        benchmarks_list = []
        if isinstance(benchmark_raw, (list, tuple, set)):
            benchmarks_list = [str(b).strip().upper() for b in benchmark_raw if str(b).strip()]
        elif isinstance(benchmark_raw, str):
            benchmarks_list = [b.strip().upper() for b in re.split(r"[\s,;]+", benchmark_raw) if b.strip()]

        clean_benchmarks = []
        for bm in benchmarks_list:
            if bm and bm not in clean_benchmarks:
                clean_benchmarks.append(bm)

        if not clean_benchmarks:
            raise HTTPException(status_code=400, detail="Не указан валидный Market Asset (Benchmark).")

        cleaned_groups = {}
        all_requested_tickers = []
        for group_name, tickers in groups.items():
            if not isinstance(tickers, list):
                continue
            clean_tickers = []
            for ticker in tickers:
                if not isinstance(ticker, str):
                    continue
                ticker = ticker.strip().upper()
                if ticker and ticker not in clean_benchmarks and ticker not in clean_tickers:
                    clean_tickers.append(ticker)
                    if ticker not in all_requested_tickers:
                        all_requested_tickers.append(ticker)
            if clean_tickers:
                cleaned_groups[str(group_name).strip() or "Group"] = clean_tickers

        if not cleaned_groups or not all_requested_tickers:
            raise HTTPException(status_code=400, detail="Нет валидных тикеров для анализа.")

        all_symbols = list(dict.fromkeys(all_requested_tickers + clean_benchmarks))
        df = get_stock_data(*all_symbols)
        if df is None or df.empty:
            raise HTTPException(status_code=404, detail="Данные для указанных тикеров не найдены в базе данных.")

        available_tickers = set(df["ticker"].astype(str).str.strip().str.upper().unique())
        missing_benchmarks = [bm for bm in clean_benchmarks if bm not in available_tickers]
        if missing_benchmarks:
            raise HTTPException(status_code=404, detail=f"Market Asset '{', '.join(missing_benchmarks)}' не найден в базе данных.")

        missing_tickers = [t for t in all_requested_tickers if t not in available_tickers]
        active_groups = {}
        for g_name, t_list in cleaned_groups.items():
            valid_t = [t for t in t_list if t in available_tickers]
            if valid_t:
                active_groups[g_name] = valid_t

        if not active_groups:
            raise HTTPException(status_code=404, detail="Ни один из указанных тикеров не найден в базе данных.")

        cleaned_asset_moves = {}
        if isinstance(asset_moves, dict):
            for t, m in asset_moves.items():
                try:
                    m = float(m)
                    if np.isfinite(m):
                        cleaned_asset_moves[str(t).strip().upper()] = m
                except (TypeError, ValueError):
                    continue

        metric = str(data.get("metric") or "ctc").strip().lower()
        session_mode = str(data.get("session_mode") or "intraday").strip().lower()
        custom_days = data.get("custom_days")
        custom_start = data.get("custom_start")
        custom_end = data.get("custom_end")
        anomaly_mode = str(data.get("anomaly_mode") or "none").strip().lower()
        anomaly_metric = str(data.get("anomaly_metric") or "gap").strip().lower()
        anomaly_op = str(data.get("anomaly_op") or "abs_gte").strip().lower()
        try:
            anomaly_threshold = float(data.get("anomaly_threshold", 5.0))
        except (ValueError, TypeError):
            anomaly_threshold = 5.0
        anomaly_target = str(data.get("anomaly_target") or "benchmark").strip().lower()

        session_bounds = None
        if metric == "session" and session_mode in ("premarket", "pre", "postmarket", "post"):
            if str(period).lower().startswith("earnings"):
                from calculations import get_ticker_earnings_reaction_dates
                lim = 8 if period == "earnings_8q" else (20 if period == "earnings_20q" else (4 if period == "earnings_4q" else (12 if period == "earnings_12q" else (16 if period == "earnings_16q" else None))))
                daily_dates_idx = pd.DatetimeIndex(pd.to_datetime(df["date"]).drop_duplicates().sort_values())
                reac_dates = []
                for t in all_symbols:
                    d_list = get_ticker_earnings_reaction_dates(t, lim, daily_dates_idx)
                    reac_dates.extend(d_list)
                reac_dates = list(dict.fromkeys(reac_dates))
                session_bounds = get_session_bounds(all_symbols, dates=reac_dates)
            else:
                from calculations import select_period
                dummy_s = df.groupby("date")["close"].mean()
                dummy_s.index = pd.to_datetime(dummy_s.index)
                _, s_d, e_d = select_period(dummy_s, period, custom_days=custom_days, custom_start=custom_start, custom_end=custom_end)
                session_bounds = get_session_bounds(all_symbols, start_date=s_d, end_date=e_d)

        analysis = calculate_groups_analysis(
            df=df,
            groups=active_groups,
            benchmarks=clean_benchmarks,
            period=period,
            benchmark_move=benchmark_move,
            benchmark_moves=benchmark_moves if isinstance(benchmark_moves, dict) else None,
            asset_moves=cleaned_asset_moves,
            metric=metric,
            session_mode=session_mode,
            session_bounds=session_bounds,
            custom_days=custom_days,
            custom_start=custom_start,
            custom_end=custom_end,
            anomaly_mode=anomaly_mode,
            anomaly_metric=anomaly_metric,
            anomaly_op=anomaly_op,
            anomaly_threshold=anomaly_threshold,
            anomaly_target=anomaly_target,
        )

        ignored_tickers = analysis.get("ignored_tickers", {})
        partial_tickers = analysis.get("partial_tickers", {})
        warning_parts = []
        if missing_tickers:
            warning_parts.append(
                f"Пропущено {len(missing_tickers)} тикеров (нет в БД): {', '.join(missing_tickers[:8])}"
                + (f" и ещё {len(missing_tickers) - 8}..." if len(missing_tickers) > 8 else "")
            )
        if ignored_tickers:
            ignored_list = list(ignored_tickers.keys()) if isinstance(ignored_tickers, dict) else list(ignored_tickers)
            warning_parts.append(
                f"Не удалось рассчитать {len(ignored_list)} тикеров (< 2 наблюдений): {', '.join(ignored_list[:8])}"
                + (f" и ещё {len(ignored_list) - 8}..." if len(ignored_list) > 8 else "")
            )
        if partial_tickers:
            partial_list = list(partial_tickers.keys())
            warning_parts.append(
                f"{len(partial_list)} тикеров имеют историю меньше периода {analysis.get('period_label_ru', period)}: {', '.join(partial_list[:6])}"
                + (f" и ещё {len(partial_list) - 6}..." if len(partial_list) > 6 else "")
            )

        warning_message = " | ".join(warning_parts) if warning_parts else None

        return {
            "groups": active_groups,
            "benchmarks": clean_benchmarks,
            "primary_benchmark": clean_benchmarks[0],
            "benchmark": clean_benchmarks[0],
            "reference": clean_benchmarks[0],
            "benchmark_moves": clean_dict(analysis.get("benchmark_moves", {})),
            "benchmark_move": benchmark_move,
            "asset_moves": cleaned_asset_moves,
            "tickers": analysis.get("tickers", []),
            "all_symbols": analysis.get("all_symbols", []),
            "period": analysis.get("period", period),
            "period_label_ru": analysis.get("period_label_ru"),
            "metric": analysis.get("metric", metric),
            "metric_label_ru": analysis.get("metric_label_ru"),
            "session_mode": analysis.get("session_mode"),
            "period_start": analysis.get("period_start"),
            "period_end": analysis.get("period_end"),
            "correlation": clean_dict(analysis.get("correlation", {})),
            "beta": clean_dict(analysis.get("beta", {})),
            "benchmark_beta": clean_dict(analysis.get("benchmark_beta", {})),
            "expected_range": clean_dict(analysis.get("expected_range", {})),
            "benchmarks_data": clean_dict(analysis.get("benchmarks_data", {})),
            "group_summary": clean_dict(analysis.get("group_summary", {})),
            "missing_tickers": missing_tickers,
            "ignored_tickers": clean_dict(ignored_tickers),
            "partial_tickers": clean_dict(partial_tickers),
            "warning": warning_message,
            "total_requested": len(all_requested_tickers),
            "total_analyzed": len(analysis.get("tickers", [])),
            "anomaly_mode": analysis.get("anomaly_mode"),
            "anomaly_metric": analysis.get("anomaly_metric"),
            "anomaly_op": analysis.get("anomaly_op"),
            "anomaly_threshold": analysis.get("anomaly_threshold"),
            "anomaly_target": analysis.get("anomaly_target"),
            "anomaly_label_ru": analysis.get("anomaly_label_ru"),
            "anomaly_days_count": analysis.get("anomaly_days_count"),
            "anomaly_dates": analysis.get("anomaly_dates"),
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/export-excel")
async def api_export_excel(request: Request):
    try:
        from fastapi.responses import Response
        data = await request.json() or {}

        rows = data.get("rows", [])
        benchmarks = data.get("benchmarks", [])
        benchmark = str(data.get("benchmark", "")).strip().upper() or "BENCHMARK"
        period = str(data.get("period", "1y"))
        is_multi = bool(data.get("is_multi_benchmark", False) or (isinstance(benchmarks, list) and len(benchmarks) > 1))

        if not rows:
            raise HTTPException(status_code=400, detail="Нет данных для экспорта.")

        if not benchmarks:
            benchmarks = [benchmark]

        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Analysis Results"

        header_fill = PatternFill(start_color="1E293B", end_color="1E293B", fill_type="solid")
        header_font = Font(name="Calibri", size=11, bold=True, color="FFFFFF")
        header_alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        border_thin = Side(border_style="thin", color="E2E8F0")
        cell_border = Border(left=border_thin, right=border_thin, top=border_thin, bottom=border_thin)

        if is_multi and len(benchmarks) > 1:
            headers = ["Ticker"]
            for bm in benchmarks:
                headers.extend([f"Corr ({bm})", f"Beta ({bm})", f"Exp Move ({bm})"])
            headers.extend(["Actual Move", "Daily Vol", "Observations", "Period"])
            ws.append(headers)

            for r in rows:
                ticker = str(r.get("ticker", "")).strip().upper()
                row_data = [ticker]
                for bm in benchmarks:
                    row_data.append(clean_value(r.get(f"correlation_{bm}") or r.get("benchmarks_metrics", {}).get(bm, {}).get("correlation")))
                    row_data.append(clean_value(r.get(f"beta_{bm}") or r.get("benchmarks_metrics", {}).get(bm, {}).get("beta")))
                    row_data.append(clean_value(r.get(f"expected_move_{bm}") or r.get("benchmarks_metrics", {}).get(bm, {}).get("expected_move")))
                row_data.extend([clean_value(r.get("actual_move")), clean_value(r.get("volatility")), clean_value(r.get("observations")), period])
                ws.append(row_data)
        else:
            headers = ["Ticker", f"Corr ({benchmark})", f"Beta ({benchmark})", f"Exp Move ({benchmark})", "Actual Move", "Difference", "Normalized", "Daily Vol", "Residual Vol", "Observations"]
            ws.append(headers)
            for r in rows:
                ws.append([
                    str(r.get("ticker", "")).strip().upper(),
                    clean_value(r.get("correlation")),
                    clean_value(r.get("beta")),
                    clean_value(r.get("expected_move")),
                    clean_value(r.get("actual_move")),
                    clean_value(r.get("difference")),
                    clean_value(r.get("normalized")),
                    clean_value(r.get("volatility")),
                    clean_value(r.get("residual_volatility")),
                    clean_value(r.get("observations"))
                ])

        ws.row_dimensions[1].height = 24
        for col in range(1, len(headers) + 1):
            cell = ws.cell(row=1, column=col)
            cell.fill = header_fill
            cell.font = header_font
            cell.alignment = header_alignment
            cell.border = cell_border

        for col in ws.columns:
            max_len = max(len(str(cell.value or '')) for cell in col)
            col_letter = get_column_letter(col[0].column)
            ws.column_dimensions[col_letter].width = max(max_len + 3, 12)

        output = io.BytesIO()
        wb.save(output)
        output.seek(0)

        filename = f"Quant_Analysis_{period}_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
        return Response(
            content=output.getvalue(),
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'}
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))



# ============================================================
# ESIGNAL TIMING & ANOMALY SCANNER ENDPOINTS
# ============================================================

from services.timing_anomaly_engine import anomaly_engine, COLOR_MAP
from services.esignal_collector import esignal_bridge
from services.excel_matrix_builder import build_excel_scanner_sheet, TOP_BLOCK_DATA, BOTTOM_BLOCK_DATA

@app.get("/scanner", response_class=HTMLResponse)
async def get_esignal_scanner_page():
    scanner_tpl = BASE_DIR / "templates" / "esignal_scanner.html"
    if scanner_tpl.exists():
        with open(scanner_tpl, "r", encoding="utf-8") as f:
            return HTMLResponse(f.read())
    return HTMLResponse("<h1>Scanner template not found</h1>", status_code=404)


@app.get("/api/scanner/data")
async def get_scanner_data(time: str = Query("05:00")):
    """
    Returns real-time and premarket anomaly statistics for all stocks
    in the 9 column blocks matching Resulting_table.
    """
    # Evaluate Top Block
    top_groups = []
    for grp in TOP_BLOCK_DATA:
        evaluated_grp = []
        for ticker, price, move_val in grp:
            # Check if live tick from eSignal exists
            live_q = esignal_bridge.live_quotes.get(ticker)
            if live_q:
                price = live_q.get("price", price)
                move_val = live_q.get("pct_move", move_val)

            res = anomaly_engine.classify_anomaly(
                ticker=ticker,
                current_price=price,
                prev_close=price / (1.0 + move_val / 100.0) if price and move_val != -100 else price,
                time_str=time
            )
            evaluated_grp.append(res)
        top_groups.append(evaluated_grp)

    # Evaluate Bottom Block
    bottom_groups = []
    for grp in BOTTOM_BLOCK_DATA:
        evaluated_grp = []
        for ticker, price, move_val in grp:
            live_q = esignal_bridge.live_quotes.get(ticker)
            if live_q:
                price = live_q.get("price", price)
                move_val = live_q.get("pct_move", move_val)

            res = anomaly_engine.classify_anomaly(
                ticker=ticker,
                current_price=price,
                prev_close=price / (1.0 + move_val / 100.0) if price and move_val != -100 else price,
                time_str=time
            )
            evaluated_grp.append(res)
        bottom_groups.append(evaluated_grp)

    baseline_info = "0.10% avg move (Early Pre-Market)" if time <= "06:00" else ("0.35% avg move (Mid Pre-Market)" if time <= "08:30" else "0.75% avg move (Market Open)")

    return {
        "time_str": time,
        "baseline_info": baseline_info,
        "top_groups": top_groups,
        "bottom_groups": bottom_groups,
        "live_count": len(esignal_bridge.live_quotes)
    }


@app.post("/api/esignal/tick")
async def receive_esignal_tick(request: Request):
    """
    Webhook receiving live streaming ticks pushed by eSignal EFS script.
    Payload: {"ticker": "MCD", "last": 238.9, "change_pct": 0.50, "volume": 1000, "time": "05:00:10"}
    """
    try:
        data = await request.json()
        esignal_bridge.on_efs_tick(data)
        return {"status": "ok", "received": data.get("ticker")}
    except Exception as e:
        return JSONResponse({"status": "error", "message": str(e)}, status_code=400)


@app.get("/api/scanner/export")
async def export_scanner_excel():
    """
    Builds and downloads the Excel file matching the screenshot with RTD formulas.
    """
    export_path = BASE_DIR / "cache" / "Resulting_table.xlsx"
    os.makedirs(export_path.parent, exist_ok=True)
    build_excel_scanner_sheet(str(export_path), use_rtd_formulas=True)
    return FileResponse(
        str(export_path),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        filename="analysis_Resulting_table.xlsx"
    )



if __name__ == "__main__":
    import uvicorn
    import socket

    def find_free_port(preferred_port=8000):
        for port in [preferred_port, 8080, 8001, 8002, 5000, 5050]:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                try:
                    s.bind(("127.0.0.1", port))
                    return port
                except OSError:
                    continue
        return preferred_port

    target_port = int(os.getenv("PORT", find_free_port(8000)))
    print("\n" + "=" * 55)
    print(f">> Helion Quant Platform: http://127.0.0.1:{target_port}")
    print(f">> Swagger API Docs:      http://127.0.0.1:{target_port}/docs")
    print("=" * 55 + "\n")
    uvicorn.run("app_fastapi:app", host="127.0.0.1", port=target_port, reload=True)
