import numpy as np
import pandas as pd


# ============================================================
# PERIODS
# ============================================================

PERIODS = {
    "2w": 14,
    "1m": 30,
    "3m": 90,
    "6m": 180,
    "ytd": "YTD",
    "1y": 365,
    "2y": 730,
    "5y": 1825,
    "custom": "custom",
    "earnings_4q": "earnings_4q",
    "earnings_8q": "earnings_8q",
    "earnings_12q": "earnings_12q",
    "earnings_16q": "earnings_16q",
    "earnings_20q": "earnings_20q",
    "earnings_all": "earnings_all",
}



# ============================================================
# PREPARE MULTI-ASSET RETURNS
# ============================================================

def adjust_series_for_splits(series: pd.Series) -> pd.Series:
    """
    Detects unadjusted stock splits (e.g. 2:1, 3:1, 4:1, 1:2, etc.) and adjusts
    historical prices backwards so that pct_change() reflects true market return.
    """
    prices = series.values.copy()
    n = len(prices)
    if n < 2:
        return series

    cum_factor = 1.0
    adjusted = np.zeros(n, dtype=float)
    adjusted[-1] = prices[-1]

    for i in range(n - 2, -1, -1):
        p_prev = prices[i]
        p_curr = prices[i + 1]
        if p_curr > 0 and p_prev > 0:
            ratio = p_prev / p_curr
            for split in (2, 3, 4, 5, 7, 8, 10, 20):
                if abs(ratio - split) / split < 0.08:
                    cum_factor *= split
                    break
                rev_split = 1.0 / split
                if abs(ratio - rev_split) / rev_split < 0.08:
                    cum_factor *= rev_split
                    break
        adjusted[i] = p_prev / cum_factor

    return pd.Series(adjusted, index=series.index)


def prepare_multi_asset_returns(
    df,
    tickers,
    metric="ctc",
    session_mode=None,
    session_bounds=None
):
    """
    Transforms multi-ticker OHLC/close data into a wide DataFrame of returns
    according to the selected calculation basis:
      - 'ctc': Close-to-Close daily return (split-adjusted)
      - 'gap': Gap % return (Open vs Previous Close)
      - 'intra': Intraday % return (Close vs Open)
      - 'session': Session % return:
          - 'premarket' / 'pre': (Open - Premarket Open) / Premarket Open
          - 'intraday' / 'intra': (Close - Open) / Open
          - 'postmarket' / 'post': (Postmarket Close - Close) / Close
    """
    if df is None or df.empty:
        return pd.DataFrame()

    data = df.copy()

    data["ticker"] = (
        data["ticker"]
        .astype(str)
        .str.strip()
        .str.upper()
    )

    clean_tickers = [
        str(t).strip().upper()
        for t in tickers
        if str(t).strip()
    ]

    data = data[data["ticker"].isin(clean_tickers)].copy()

    if data.empty:
        return pd.DataFrame()

    data["date"] = pd.to_datetime(data["date"], errors="coerce")
    data["close"] = pd.to_numeric(data["close"], errors="coerce")
    if "open" in data.columns:
        data["open"] = pd.to_numeric(data["open"], errors="coerce")
    if "prev_close" in data.columns:
        data["prev_close"] = pd.to_numeric(data["prev_close"], errors="coerce")
    if "gap" in data.columns:
        data["gap"] = pd.to_numeric(data["gap"], errors="coerce")

    data = data.dropna(subset=["date", "close"])
    data = data.drop_duplicates(subset=["ticker", "date"], keep="last")
    data = data.sort_values(["ticker", "date"])

    metric_clean = str(metric or "ctc").strip().lower()
    session_mode_clean = str(session_mode or "intraday").strip().lower()

    if metric_clean == "gap":
        # Gap return in decimal: (open - prev_close) / prev_close
        if "gap" in data.columns and data["gap"].notnull().any():
            data["return"] = data["gap"] / 100.0
        elif "open" in data.columns and "prev_close" in data.columns:
            data["return"] = (data["open"] - data["prev_close"]) / data["prev_close"]
        else:
            data["close_adj"] = data.groupby("ticker")["close"].transform(adjust_series_for_splits)
            data["return"] = data.groupby("ticker")["close_adj"].pct_change()

    elif metric_clean == "intra" or (metric_clean == "session" and session_mode_clean in ("intraday", "intra")):
        # Intraday regular trading hours (09:30 -> 16:00): (close - open) / open
        if "open" in data.columns:
            data["return"] = (data["close"] - data["open"]) / data["open"]
        else:
            data["close_adj"] = data.groupby("ticker")["close"].transform(adjust_series_for_splits)
            data["return"] = data.groupby("ticker")["close_adj"].pct_change()

    elif metric_clean == "session" and session_mode_clean in ("premarket", "pre"):
        # Premarket session (04:00 -> 09:30): (open - pre_open) / pre_open
        if session_bounds and "open" in data.columns:
            data["date_str"] = data["date"].dt.strftime("%Y-%m-%d")
            pre_returns = []
            for _, r in data.iterrows():
                t = r["ticker"]
                d_str = r["date_str"]
                o_val = r["open"]
                b_info = session_bounds.get((t, d_str)) or session_bounds.get(f"{t}_{d_str}") or {}
                pre_open = b_info.get("pre_open")
                if pre_open is None and "first" in b_info:
                    f_time, f_price = b_info["first"]
                    if f_time < "09:30:00":
                        pre_open = f_price

                if pre_open is not None and pre_open > 0 and o_val is not None and np.isfinite(o_val):
                    pre_returns.append((o_val - pre_open) / pre_open)
                else:
                    pre_returns.append(np.nan)
            data["return"] = pre_returns
        else:
            # Fallback if no premarket trade bounds: gap return
            if "gap" in data.columns and data["gap"].notnull().any():
                data["return"] = data["gap"] / 100.0
            elif "open" in data.columns and "prev_close" in data.columns:
                data["return"] = (data["open"] - data["prev_close"]) / data["prev_close"]
            else:
                data["close_adj"] = data.groupby("ticker")["close"].transform(adjust_series_for_splits)
                data["return"] = data.groupby("ticker")["close_adj"].pct_change()

    elif metric_clean == "session" and session_mode_clean in ("postmarket", "post"):
        # Postmarket session (16:00 -> 20:00): (post_close - close) / close
        if session_bounds and "close" in data.columns:
            data["date_str"] = data["date"].dt.strftime("%Y-%m-%d")
            post_returns = []
            for _, r in data.iterrows():
                t = r["ticker"]
                d_str = r["date_str"]
                c_val = r["close"]
                b_info = session_bounds.get((t, d_str)) or session_bounds.get(f"{t}_{d_str}") or {}
                post_close = b_info.get("post_close")
                if post_close is None and "last" in b_info:
                    l_time, l_price = b_info["last"]
                    if l_time > "16:00:00":
                        post_close = l_price

                if post_close is not None and c_val is not None and c_val > 0 and np.isfinite(c_val):
                    post_returns.append((post_close - c_val) / c_val)
                else:
                    post_returns.append(np.nan)
            data["return"] = post_returns
        else:
            data["return"] = np.nan

    else:
        # Default: Close-to-Close (CTC) with split adjustments
        data["close_adj"] = data.groupby("ticker")["close"].transform(adjust_series_for_splits)
        data["return"] = data.groupby("ticker")["close_adj"].pct_change()

    returns = (
        data.pivot(index="date", columns="ticker", values="return")
        .sort_index()
    )

    return returns


# ============================================================
# STATISTICAL HELPERS (SINGLE PAIR)
# ============================================================

def pearson_correlation(x, y):
    x = np.asarray(x, dtype=float)
    y = np.asarray(y, dtype=float)

    mask = np.isfinite(x) & np.isfinite(y)
    x = x[mask]
    y = y[mask]

    if len(x) < 2:
        return np.nan

    x_mean = np.mean(x)
    y_mean = np.mean(y)

    num = np.sum((x - x_mean) * (y - y_mean))
    den = np.sqrt(np.sum((x - x_mean) ** 2) * np.sum((y - y_mean) ** 2))

    if den == 0:
        return np.nan

    return float(num / den)


def sample_covariance(x, y):
    x = np.asarray(x, dtype=float)
    y = np.asarray(y, dtype=float)

    mask = np.isfinite(x) & np.isfinite(y)
    x = x[mask]
    y = y[mask]

    if len(x) < 2:
        return np.nan

    return float(np.sum((x - np.mean(x)) * (y - np.mean(y))) / (len(x) - 1))


def sample_variance(x):
    x = np.asarray(x, dtype=float)
    x = x[np.isfinite(x)]

    if len(x) < 2:
        return np.nan

    return float(np.sum((x - np.mean(x)) ** 2) / (len(x) - 1))


def calculate_beta(asset_returns, market_returns):
    covariance = sample_covariance(asset_returns, market_returns)
    market_variance = sample_variance(market_returns)

    if (
        not np.isfinite(covariance)
        or not np.isfinite(market_variance)
        or market_variance == 0
    ):
        return np.nan

    return float(covariance / market_variance)


def calculate_volatility(returns):
    returns = np.asarray(returns, dtype=float)
    returns = returns[np.isfinite(returns)]

    if len(returns) < 2:
        return np.nan

    return float(np.std(returns, ddof=1))


# ============================================================
# PERIOD SELECTION
# ============================================================

def get_ticker_earnings_reaction_dates(ticker, limit, returns_index):
    """
    Retrieves released earnings reports for `ticker` and computes strictly 1 reaction trading day per report:
    - BMO (premarket): report day itself (market opens after release).
    - AMC (postmarket): next trading day (first regular session after release).
    """
    from database import get_ticker_reports
    from services.earnings_reaction_service import is_report_already_released
    from services.fmp_service import fmp_client

    ticker_clean = str(ticker).strip().upper()
    reports_df = get_ticker_reports(ticker_clean)
    all_reps = []
    seen_dates = set()

    if not reports_df.empty:
        for r in reports_df.to_dict("records"):
            d = str(r.get("date", ""))
            if d and d not in seen_dates:
                seen_dates.add(d)
                all_reps.append(r)

    # Fallback to FMP if DB had few
    if len(all_reps) < (limit or 8):
        try:
            fmp_reps = fmp_client.get_earnings_history(ticker_clean, limit=(limit or 60) + 10)
            for fr in fmp_reps:
                d = str(fr.get("date", ""))
                if d and d not in seen_dates:
                    seen_dates.add(d)
                    all_reps.append(fr)
        except Exception:
            pass

    past_reps = [r for r in all_reps if is_report_already_released(r)]
    past_reps.sort(key=lambda x: str(x.get("date", "")), reverse=True)

    target_reps = past_reps[:limit] if limit is not None else past_reps

    if hasattr(returns_index, "strftime"):
        returns_date_strs = returns_index.strftime("%Y-%m-%d")
    elif hasattr(returns_index, "dt"):
        returns_date_strs = returns_index.dt.strftime("%Y-%m-%d").values
    else:
        returns_date_strs = pd.to_datetime(returns_index).strftime("%Y-%m-%d")
    returns_date_strs = np.asarray(returns_date_strs)
    reaction_dates = []

    for r in target_reps:
        p_date = str(r.get("date", ""))
        if not p_date:
            continue
        session_str = str(r.get("time") or "bmo").lower()
        is_bmo = "bmo" in session_str or "before" in session_str or "bfo" in session_str or any(h in session_str for h in ["00:", "01:", "02:", "03:", "04:", "05:", "06:", "07:", "08:", "09:"])

        matching_idx = np.where(returns_date_strs == p_date)[0]
        if len(matching_idx) > 0:
            idx_val = matching_idx[0]
        else:
            prior_idx = np.where(returns_date_strs <= p_date)[0]
            idx_val = prior_idx[-1] if len(prior_idx) > 0 else None

        if idx_val is not None:
            reaction_idx = idx_val if is_bmo else idx_val + 1
            if 0 <= reaction_idx < len(returns_date_strs):
                reaction_dates.append(pd.Timestamp(returns_date_strs[reaction_idx]))

    # Deduplicate and sort chronologically
    reaction_dates = list(dict.fromkeys(reaction_dates))
    reaction_dates.sort()
    return reaction_dates


def select_period(returns, period, custom_days=None, custom_start=None, custom_end=None):
    if returns.empty:
        raise ValueError("Нет данных для расчёта.")

    period = str(period or "1y").lower()

    if period not in PERIODS:
        raise ValueError(f"Неизвестный период: {period}")

    last_date = returns.index.max()

    if period.startswith("earnings"):
        start_date = returns.index.min()
        return returns.copy(), start_date, last_date

    if period == "custom":
        has_days = custom_days is not None and str(custom_days).strip() != ""
        if has_days:
            try:
                c_days = int(custom_days)
                start_date = last_date - pd.Timedelta(days=c_days)
                end_date = last_date
            except (ValueError, TypeError):
                start_date = last_date - pd.Timedelta(days=14)
                end_date = last_date
        elif (custom_start is not None and str(custom_start).strip() != "") or (custom_end is not None and str(custom_end).strip() != ""):
            start_date = pd.to_datetime(custom_start) if (custom_start and str(custom_start).strip()) else returns.index.min()
            end_date = pd.to_datetime(custom_end) if (custom_end and str(custom_end).strip()) else last_date
        else:
            start_date = last_date - pd.Timedelta(days=14)
            end_date = last_date

        period_returns = returns[(returns.index >= start_date) & (returns.index <= end_date)].copy()
        return period_returns, start_date, end_date

    if period == "ytd":
        start_date = pd.Timestamp(year=last_date.year, month=1, day=1)
    else:
        days = PERIODS[period]
        start_date = last_date - pd.Timedelta(days=days)

    period_returns = returns[returns.index >= start_date].copy()

    return period_returns, start_date, last_date


# ============================================================
# RUSSIAN PERIOD LABELS & DURATION FORMATTERS
# ============================================================

PERIOD_LABELS_RU = {
    "2w": "2 недели",
    "1m": "1 месяц",
    "3m": "3 месяца",
    "6m": "6 месяцев",
    "ytd": "С начала года",
    "1y": "1 год",
    "2y": "2 года",
    "5y": "5 лет",
    "custom": "Свой период",
    "earnings_4q": "4 отчета",
    "earnings_8q": "8 отчетов",
    "earnings_12q": "12 отчетов",
    "earnings_16q": "16 отчетов",
    "earnings_20q": "20 отчетов",
    "earnings_all": "Все отчеты",
}

def get_period_label_ru(period, custom_days=None, custom_start=None, custom_end=None):
    p = str(period or "1y").lower()
    if p == "custom":
        if custom_days and str(custom_days).strip() != "":
            return f"{custom_days} дн."
        if custom_start and custom_end:
            return f"{custom_start} — {custom_end}"
        return "Свой период"
    return PERIOD_LABELS_RU.get(p, f"{period}")


def get_metric_label_ru(metric="ctc", session_mode=None):
    m = str(metric or "ctc").strip().lower()
    sm = str(session_mode or "intraday").strip().lower()
    if m == "gap":
        return "Gap %"
    elif m == "intra":
        return "Intraday %"
    elif m == "session":
        if sm in ("premarket", "pre"):
            return "Session: Premarket"
        elif sm in ("postmarket", "post"):
            return "Session: Postmarket"
        else:
            return "Session: Intraday"
    return "Close-to-Close"


def format_history_duration_ru(days, obs_count=None):
    """
    Returns natural Russian duration string, e.g. "1 месяц", "2 недели", "25 дней", "1.5 месяца".
    """
    if days is None or days < 0:
        return f"{obs_count or 0} торг. дн."

    days = int(days)
    if days < 14:
        if days == 1:
            return "1 день"
        elif 2 <= days <= 4:
            return f"{days} дня"
        else:
            return f"{days} дней"
    elif days < 30:
        weeks = round(days / 7)
        if weeks <= 1:
            return "1 неделю"
        elif 2 <= weeks <= 4:
            return f"{weeks} недели"
        else:
            return f"{weeks} недель"
    elif days < 60:
        return "1 месяц"
    elif days < 360:
        months = round(days / 30.4, 1)
        if months.is_integer() or months == int(months):
            m_int = int(months)
            if m_int == 1:
                return "1 месяц"
            elif 2 <= m_int <= 4:
                return f"{m_int} месяца"
            else:
                return f"{m_int} месяцев"
        return f"{months} мес."
    else:
        years = round(days / 365.25, 1)
        if years.is_integer() or years == int(years):
            y_int = int(years)
            if y_int == 1:
                return "1 год"
            elif 2 <= y_int <= 4:
                return f"{y_int} года"
            else:
                return f"{y_int} лет"
        return f"{years} г."


# ============================================================
# PERIOD DATA HISTORY & SUFFICIENCY ANALYSIS
# ============================================================

def get_ticker_history_info(
    ticker,
    df,
    period_returns,
    start_date,
    last_date,
    period,
    custom_days=None,
    custom_start=None,
    custom_end=None
):
    """
    Analyzes historical availability for a ticker relative to the selected period.
    Returns a dict with:
      - is_sufficient: bool (True if covers full required period, False if partial)
      - is_partial: bool (True if historical span or observations are shorter than selected period)
      - has_data: bool
      - history_days: int
      - history_period_ru: str (e.g. "1 месяц", "2 недели", "25 дней")
      - history_text: str (e.g. "данные акции существуют за 1 месяц")
      - history_start: str (YYYY-MM-DD)
      - history_end: str (YYYY-MM-DD)
      - history_observations: int
      - selected_period: str
      - selected_period_label: str
      - reason: str or None
    """
    period_str = str(period or "1y").lower()
    period_label_ru = get_period_label_ru(
        period_str,
        custom_days=custom_days,
        custom_start=custom_start,
        custom_end=custom_end
    )

    if df is None or df.empty or ticker not in period_returns.columns:
        return {
            "is_sufficient": False,
            "is_partial": True,
            "has_data": False,
            "history_days": 0,
            "history_period_ru": "0 дней",
            "history_text": "данные акции отсутствуют за выбранный период",
            "history_start": None,
            "history_end": None,
            "history_observations": 0,
            "selected_period": period_str,
            "selected_period_label": period_label_ru,
            "reason": "Нет данных доходности за выбранный период"
        }

    ticker_clean = str(ticker).strip().upper()
    df_tickers = df["ticker"].astype(str).str.strip().str.upper()
    t_df = df[df_tickers == ticker_clean]

    if t_df.empty:
        return {
            "is_sufficient": False,
            "is_partial": True,
            "has_data": False,
            "history_days": 0,
            "history_period_ru": "0 дней",
            "history_text": "данные по активу отсутствуют в базе",
            "history_start": None,
            "history_end": None,
            "history_observations": 0,
            "selected_period": period_str,
            "selected_period_label": period_label_ru,
            "reason": "Нет данных по активу в базе"
        }

    dates = pd.to_datetime(t_df["date"], errors="coerce").dropna()
    if dates.empty:
        return {
            "is_sufficient": False,
            "is_partial": True,
            "has_data": False,
            "history_days": 0,
            "history_period_ru": "0 дней",
            "history_text": "некорректные даты в базе данных",
            "history_start": None,
            "history_end": None,
            "history_observations": 0,
            "selected_period": period_str,
            "selected_period_label": period_label_ru,
            "reason": "Некорректные даты в базе"
        }

    min_date = dates.min()
    max_date = dates.max()
    history_span_days = max(1, (max_date - min_date).days)

    # Required calendar days for the chosen period
    if period_str == "ytd":
        start_of_year = pd.Timestamp(year=last_date.year, month=1, day=1)
        required_days = max(1, (last_date - start_of_year).days)
    elif period_str.startswith("earnings"):
        required_days = 365
    elif period_str == "custom":
        if custom_days:
            try:
                required_days = max(1, int(custom_days))
            except (ValueError, TypeError):
                required_days = 14
        else:
            required_days = max(1, (last_date - start_date).days)
    else:
        req = PERIODS.get(period_str, 365)
        required_days = req if isinstance(req, (int, float)) else 365

    t_returns = period_returns[ticker].dropna()
    obs_count = len(t_returns)
    total_window_trading_days = len(period_returns)

    # Human-friendly duration string in Russian
    history_period_ru = format_history_duration_ru(history_span_days, obs_count)
    history_text = f"данные акции существуют за {history_period_ru}"

    if obs_count < 2:
        return {
            "is_sufficient": False,
            "is_partial": True,
            "has_data": True,
            "history_days": history_span_days,
            "history_period_ru": history_period_ru,
            "history_text": history_text,
            "history_start": min_date.strftime("%Y-%m-%d"),
            "history_end": max_date.strftime("%Y-%m-%d"),
            "history_observations": obs_count,
            "selected_period": period_str,
            "selected_period_label": period_label_ru,
            "reason": f"Недостаточно торговых наблюдений ({obs_count} дн.)"
        }

    first_valid_period_date = t_returns.index.min()
    last_valid_period_date = t_returns.index.max()
    first_window_date = period_returns.index.min()
    last_window_date = period_returns.index.max()

    is_partial = False
    reasons = []

    # 1. Check if the entire history in DB is noticeably shorter than the period
    # (using 7 calendar days margin to account for weekends / holiday starts)
    if history_span_days < (required_days - 7):
        is_partial = True
        reasons.append(f"История актива ({history_period_ru}) меньше периода {period_label_ru}")

    # 2. If the stock began trading noticeably after the period started (e.g. IPO)
    if (first_valid_period_date - first_window_date).days > 7:
        is_partial = True
        reasons.append(f"Торги начались {first_valid_period_date.strftime('%d.%m.%Y')}")

    # 3. If the stock stopped trading noticeably before the period ended
    if (last_window_date - last_valid_period_date).days > 7:
        is_partial = True
        reasons.append(f"Торги прекратились {last_valid_period_date.strftime('%d.%m.%Y')}")

    # 4. Observation ratio compared to total trading days in period
    if total_window_trading_days > 0 and (obs_count / total_window_trading_days) < 0.70:
        is_partial = True
        reasons.append(f"{obs_count} из {total_window_trading_days} торг. дней")

    return {
        "is_sufficient": not is_partial,
        "is_partial": is_partial,
        "has_data": True,
        "history_days": history_span_days,
        "history_period_ru": history_period_ru,
        "history_text": history_text,
        "history_start": min_date.strftime("%Y-%m-%d"),
        "history_end": max_date.strftime("%Y-%m-%d"),
        "history_observations": obs_count,
        "selected_period": period_str,
        "selected_period_label": period_label_ru,
        "reason": " | ".join(reasons) if reasons else None
    }


def check_ticker_period_sufficiency(
    ticker,
    df,
    period_returns,
    start_date,
    last_date,
    period,
    custom_days=None,
    custom_start=None,
    custom_end=None
):
    info = get_ticker_history_info(
        ticker,
        df,
        period_returns,
        start_date,
        last_date,
        period,
        custom_days=custom_days,
        custom_start=custom_start,
        custom_end=custom_end
    )
    return info["is_sufficient"], info.get("reason")


# ============================================================
# NORMALIZED METRICS
# ============================================================

def calculate_normalized_move(actual_move, beta):
    if not np.isfinite(actual_move) or not np.isfinite(beta) or beta == 0:
        return np.nan
    return float(actual_move / beta)


def calculate_normalized_difference(normalized_move, benchmark_move):
    if not np.isfinite(normalized_move) or not np.isfinite(benchmark_move):
        return np.nan
    return float(normalized_move - benchmark_move)


# ============================================================
# VECTORIZED GROUP & PORTFOLIO ANALYSIS (300+ TICKER OPTIMIZED)
# ============================================================

def calculate_groups_analysis(
    df,
    groups,
    benchmark=None,
    benchmarks=None,
    period="1y",
    reference=None,
    benchmark_move=0.01,
    benchmark_moves=None,
    asset_moves=None,
    metric="ctc",
    session_mode=None,
    session_bounds=None,
    custom_days=None,
    custom_start=None,
    custom_end=None,
    anomaly_mode=None,
    anomaly_metric="gap",
    anomaly_op="abs_gte",
    anomaly_threshold=5.0,
    anomaly_target="benchmark",
):
    import re

    metric_clean = str(metric or "ctc").strip().lower()
    session_mode_clean = str(session_mode or "intraday").strip().lower()
    metric_label_ru = get_metric_label_ru(metric_clean, session_mode_clean)
    period_label_ru = get_period_label_ru(
        period,
        custom_days=custom_days,
        custom_start=custom_start,
        custom_end=custom_end
    )

    # Normalize benchmarks list
    benchmarks_list = []
    if benchmarks is not None:
        if isinstance(benchmarks, (list, tuple, set)):
            benchmarks_list = [str(b).strip().upper() for b in benchmarks if str(b).strip()]
        elif isinstance(benchmarks, str):
            benchmarks_list = [b.strip().upper() for b in re.split(r"[\s,;]+", benchmarks) if b.strip()]

    if not benchmarks_list:
        raw_bm = benchmark if benchmark is not None else reference
        if raw_bm is not None:
            if isinstance(raw_bm, (list, tuple, set)):
                benchmarks_list = [str(b).strip().upper() for b in raw_bm if str(b).strip()]
            elif isinstance(raw_bm, str):
                benchmarks_list = [b.strip().upper() for b in re.split(r"[\s,;]+", raw_bm) if b.strip()]

    # Deduplicate while preserving order
    clean_benchmarks = []
    for bm in benchmarks_list:
        if bm and bm not in clean_benchmarks:
            clean_benchmarks.append(bm)

    if not clean_benchmarks:
        raise ValueError("Не указан Market Asset (Benchmark).")

    primary_benchmark = clean_benchmarks[0]

    # Normalize default benchmark move
    try:
        benchmark_move = float(benchmark_move)
    except (TypeError, ValueError):
        raise ValueError("Некорректное движение benchmark.")

    if not np.isfinite(benchmark_move):
        raise ValueError("Benchmark Move должен быть числом.")

    # Normalize per-benchmark moves
    cleaned_benchmark_moves = {}
    if isinstance(benchmark_moves, dict):
        for b_name, b_mv in benchmark_moves.items():
            b_name = str(b_name).strip().upper()
            try:
                b_mv_float = float(b_mv)
                if np.isfinite(b_mv_float):
                    cleaned_benchmark_moves[b_name] = b_mv_float
            except (TypeError, ValueError):
                continue

    for bm in clean_benchmarks:
        if bm not in cleaned_benchmark_moves:
            cleaned_benchmark_moves[bm] = benchmark_move

    # Normalize asset moves
    if asset_moves is None:
        asset_moves = {}

    cleaned_asset_moves = {}
    for ticker, move in asset_moves.items():
        ticker = str(ticker).strip().upper()
        try:
            move = float(move)
        except (TypeError, ValueError):
            continue
        if np.isfinite(move):
            cleaned_asset_moves[ticker] = move

    # Extract all unique tickers from groups
    all_tickers = []
    for group_tickers in groups.values():
        for ticker in group_tickers:
            ticker = str(ticker).strip().upper()
            if ticker and ticker not in all_tickers:
                all_tickers.append(ticker)

    # Ensure all benchmarks are in the list of symbols to fetch
    for bm in clean_benchmarks:
        if bm not in all_tickers:
            all_tickers.append(bm)

    # 1. Prepare Returns Matrix
    returns = prepare_multi_asset_returns(
        df,
        all_tickers,
        metric=metric_clean,
        session_mode=session_mode_clean,
        session_bounds=session_bounds
    )
    if returns.empty:
        raise ValueError("Не удалось получить доходности активов.")

    available_symbols = list(returns.columns)
    missing_benchmarks = [bm for bm in clean_benchmarks if bm not in available_symbols]
    if missing_benchmarks:
        raise ValueError(f"Нет данных для Market Asset: {', '.join(missing_benchmarks)}")

    is_earnings_period = str(period).lower().startswith("earnings")
    earnings_limit = None
    if period == "earnings_4q":
        earnings_limit = 4
    elif period == "earnings_8q":
        earnings_limit = 8
    elif period == "earnings_12q":
        earnings_limit = 12
    elif period == "earnings_16q":
        earnings_limit = 16
    elif period == "earnings_20q":
        earnings_limit = 20

    ticker_reaction_dates = {}
    if is_earnings_period:
        for ticker in all_tickers:
            if ticker not in clean_benchmarks:
                ticker_reaction_dates[ticker] = get_ticker_earnings_reaction_dates(ticker, earnings_limit, returns.index)

    # 2. Slice for selected Period
    period_returns, start_date, last_date = select_period(
        returns,
        period,
        custom_days=custom_days,
        custom_start=custom_start,
        custom_end=custom_end
    )
    if period_returns.empty:
        raise ValueError("Нет данных за выбранный период.")

    # 2.1 Anomaly Filtering (if enabled)
    anomaly_mode_clean = str(anomaly_mode or "none").strip().lower()
    anomaly_metric_clean = str(anomaly_metric or "gap").strip().lower()
    anomaly_op_clean = str(anomaly_op or "abs_gte").strip().lower()
    anomaly_target_clean = str(anomaly_target or "benchmark").strip().lower()
    try:
        thresh = float(anomaly_threshold)
    except (ValueError, TypeError):
        thresh = 5.0

    anomaly_label_ru = None
    anomaly_days_count = len(period_returns)

    if anomaly_mode_clean in ("only_anomaly", "no_anomaly"):
        op_symbol_map = {
            "gt": ">",
            "gte": "≥",
            "lt": "<",
            "lte": "≤",
            "abs_gte": "|X| ≥",
            "abs_lt": "|X| <"
        }
        op_sym = op_symbol_map.get(anomaly_op_clean, anomaly_op_clean)
        metric_name_map = {
            "gap": "Gap %",
            "ctc": "Close-to-Close",
            "intra": "Intraday %",
            "session": f"Session {session_mode_clean}"
        }
        m_name = metric_name_map.get(anomaly_metric_clean, anomaly_metric_clean.upper())
        prefix = "Аномалии" if anomaly_mode_clean == "only_anomaly" else "Без аномалий"

        if anomaly_metric_clean == metric_clean:
            anomaly_returns = returns.copy()
        else:
            anomaly_returns = prepare_multi_asset_returns(
                df,
                all_tickers,
                metric=anomaly_metric_clean,
                session_mode=session_mode_clean,
                session_bounds=session_bounds
            )

        if not anomaly_returns.empty:
            period_anomaly = anomaly_returns.reindex(period_returns.index) * 100.0

            if anomaly_target_clean == "benchmark":
                bm_col = primary_benchmark if primary_benchmark in period_anomaly.columns else None
                if bm_col and bm_col in period_anomaly.columns:
                    target_df = period_anomaly[[bm_col]]
                else:
                    target_df = period_anomaly
                anomaly_label_ru = f"{prefix}: {primary_benchmark} {m_name} {op_sym} {thresh}%"
            elif anomaly_target_clean == "either":
                target_df = period_anomaly
                anomaly_label_ru = f"{prefix}: Любой актив {m_name} {op_sym} {thresh}%"
            else:  # "asset"
                asset_cols = [c for c in period_anomaly.columns if c not in clean_benchmarks]
                target_df = period_anomaly[asset_cols] if asset_cols else period_anomaly
                anomaly_label_ru = f"{prefix}: Активы {m_name} {op_sym} {thresh}%"

            # Fully vectorized condition check across all pandas versions
            if anomaly_op_clean == "gt":
                cond_matrix = target_df > thresh
            elif anomaly_op_clean == "gte":
                cond_matrix = target_df >= thresh
            elif anomaly_op_clean == "lt":
                cond_matrix = target_df < thresh
            elif anomaly_op_clean == "lte":
                cond_matrix = target_df <= thresh
            elif anomaly_op_clean == "abs_lt":
                cond_matrix = target_df.abs() < thresh
            else:  # "abs_gte"
                cond_matrix = target_df.abs() >= thresh

            if anomaly_mode_clean == "no_anomaly":
                cond_matrix = ~cond_matrix

            mask = cond_matrix.any(axis=1)
            matched_dates = period_anomaly.index[mask]

            if len(matched_dates) < 2:
                raise ValueError(
                    f"По фильтру ({anomaly_label_ru}) найдено {len(matched_dates)} торговых дней. Для расчёта корреляции требуется минимум 2 дня."
                )

            period_returns = period_returns.loc[matched_dates].copy()
            anomaly_days_count = len(period_returns)
            anomaly_label_ru += f" ({anomaly_days_count} дн.)"

    # 3. Validate benchmarks data sufficiency for the selected period
    for bm in clean_benchmarks:
        if not is_earnings_period and anomaly_mode_clean == "none":
            bm_ok, bm_reason = check_ticker_period_sufficiency(
                bm,
                df,
                period_returns,
                start_date,
                last_date,
                period,
                custom_days=custom_days,
                custom_start=custom_start,
                custom_end=custom_end
            )
            if not bm_ok:
                raise ValueError(f"Market Asset (Benchmark) '{bm}' не имеет достаточной истории торгов за выбранный период ({period_label_ru}): {bm_reason}")
        else:
            if bm not in period_returns.columns or period_returns[bm].dropna().empty or len(period_returns[bm].dropna()) < 2:
                raise ValueError(f"Market Asset (Benchmark) '{bm}' имеет менее 2 наблюдений доходности.")

    # 4. Check history info & sufficiency for each asset ticker
    valid_symbols = []
    ignored_tickers = {}
    partial_tickers = {}
    tickers_history_info = {}

    if is_earnings_period:
        for bm in clean_benchmarks:
            bm_rets = returns[bm].dropna() if bm in returns.columns else pd.Series(dtype=float)
            obs_count = len(bm_rets)
            tickers_history_info[bm] = {
                "is_sufficient": obs_count >= 2,
                "is_partial": False,
                "has_data": obs_count > 0,
                "history_days": obs_count,
                "history_period_ru": "Benchmark",
                "history_text": f"данные бенчмарка ({obs_count} дн.)",
                "history_start": bm_rets.index.min().strftime("%Y-%m-%d") if not bm_rets.empty else None,
                "history_end": bm_rets.index.max().strftime("%Y-%m-%d") if not bm_rets.empty else None,
                "history_observations": obs_count,
                "selected_period": period,
                "selected_period_label": period_label_ru,
                "reason": None
            }

    for ticker in all_tickers:
        if ticker in clean_benchmarks:
            continue
        if ticker not in period_returns.columns:
            ignored_tickers[ticker] = "Нет данных в выборке доходностей"
            continue

        if is_earnings_period:
            reac_dates = ticker_reaction_dates.get(ticker, [])
            t_rets = returns.loc[reac_dates, ticker].dropna() if reac_dates else pd.Series(dtype=float)
            obs_count = len(t_rets)
            req_obs = earnings_limit or 2
            is_partial = (obs_count < req_obs) if earnings_limit else False

            h_info = {
                "is_sufficient": obs_count >= 2,
                "is_partial": is_partial,
                "has_data": obs_count > 0,
                "history_days": obs_count,
                "history_period_ru": f"{obs_count} отч.",
                "history_text": f"данные по {obs_count} отчетам",
                "history_start": reac_dates[0].strftime("%Y-%m-%d") if reac_dates else None,
                "history_end": reac_dates[-1].strftime("%Y-%m-%d") if reac_dates else None,
                "history_observations": obs_count,
                "selected_period": period,
                "selected_period_label": period_label_ru,
                "reason": f"Найдено {obs_count} отчетов (требуется {req_obs})" if (is_partial and obs_count < req_obs) else None
            }
            tickers_history_info[ticker] = h_info

            if obs_count >= 2:
                valid_symbols.append(ticker)
                if is_partial:
                    partial_tickers[ticker] = h_info
            else:
                ignored_tickers[ticker] = h_info.get("reason") or "Недостаточно отчетов (< 2 отчетов)"
        else:
            h_info = get_ticker_history_info(
                ticker,
                df,
                period_returns,
                start_date,
                last_date,
                period,
                custom_days=custom_days,
                custom_start=custom_start,
                custom_end=custom_end
            )
            tickers_history_info[ticker] = h_info

            # If ticker has at least 2 trading observations, calculate metrics
            if h_info.get("history_observations", 0) >= 2:
                valid_symbols.append(ticker)
                if h_info.get("is_partial"):
                    partial_tickers[ticker] = h_info
            else:
                ignored_tickers[ticker] = h_info.get("reason") or "Недостаточно торговых наблюдений (< 2 дней)"

    # Active symbols including benchmarks
    symbols = clean_benchmarks + [s for s in valid_symbols if s not in clean_benchmarks]

    all_reac_dates_union = []
    if is_earnings_period:
        for d_list in ticker_reaction_dates.values():
            all_reac_dates_union.extend(d_list)
        all_reac_dates_union = list(dict.fromkeys(all_reac_dates_union))

    if not is_earnings_period:
        # 3. Vectorized Pearson Correlation Matrix
        corr_df = period_returns[symbols].corr(method="pearson")
        correlation = {}
        for col_a in symbols:
            correlation[col_a] = {}
            for col_b in symbols:
                val = corr_df.loc[col_a, col_b] if (col_a in corr_df.index and col_b in corr_df.columns) else np.nan
                correlation[col_a][col_b] = float(val) if np.isfinite(val) else None

        # 4. Vectorized Covariance & Beta Matrix
        cov_df = period_returns[symbols].cov(ddof=1)
        variances = np.diag(cov_df.values)

        with np.errstate(divide="ignore", invalid="ignore"):
            var_col = variances[np.newaxis, :]
            beta_matrix_vals = np.where(var_col > 0, cov_df.values / var_col, np.nan)

        beta_df = pd.DataFrame(beta_matrix_vals, index=symbols, columns=symbols)
        for s in symbols:
            if s in beta_df.index:
                beta_df.loc[s, s] = 1.0

        beta = {}
        for col_a in symbols:
            beta[col_a] = {}
            for col_b in symbols:
                val = beta_df.loc[col_a, col_b] if (col_a in beta_df.index and col_b in beta_df.columns) else np.nan
                beta[col_a][col_b] = float(val) if np.isfinite(val) else None

        # 5. Volatilities of all assets
        std_series = period_returns[symbols].std(ddof=1)
    else:
        # Pairwise calculation on earnings reaction days
        correlation = {}
        beta = {}
        std_series = {}

        for col_a in symbols:
            correlation[col_a] = {}
            beta[col_a] = {}

        for col_a in symbols:
            dates_a = ticker_reaction_dates.get(col_a)
            if dates_a:
                r_a_series = returns.loc[dates_a, col_a].dropna()
                std_series[col_a] = float(np.std(r_a_series, ddof=1)) if len(r_a_series) >= 2 else np.nan
            else:
                r_a_series = returns[col_a].dropna()
                std_series[col_a] = float(np.std(r_a_series, ddof=1)) if len(r_a_series) >= 2 else np.nan

            for col_b in symbols:
                if col_a == col_b:
                    correlation[col_a][col_b] = 1.0
                    beta[col_a][col_b] = 1.0
                    continue

                target_dates = ticker_reaction_dates.get(col_a) or ticker_reaction_dates.get(col_b) or all_reac_dates_union
                if target_dates:
                    r_a = returns.loc[target_dates, col_a]
                    r_b = returns.loc[target_dates, col_b]
                    valid = np.isfinite(r_a) & np.isfinite(r_b)
                    if int(np.sum(valid)) >= 2:
                        c_val = pearson_correlation(r_a[valid], r_b[valid])
                        b_val = calculate_beta(r_a[valid], r_b[valid])
                        correlation[col_a][col_b] = float(c_val) if np.isfinite(c_val) else None
                        beta[col_a][col_b] = float(b_val) if np.isfinite(b_val) else None
                    else:
                        correlation[col_a][col_b] = None
                        beta[col_a][col_b] = None
                else:
                    correlation[col_a][col_b] = None
                    beta[col_a][col_b] = None

    # 6. Calculate Metrics for Each Benchmark
    benchmarks_data = {}

    for bm in clean_benchmarks:
        bm_beta = {}
        for s in symbols:
            bm_beta[s] = beta.get(s, {}).get(bm)

        bm_current_move = cleaned_benchmark_moves.get(bm, benchmark_move)
        expected_range_bm = {}

        for ticker in symbols:
            h_info = tickers_history_info.get(ticker) or get_ticker_history_info(
                ticker,
                df,
                period_returns,
                start_date,
                last_date,
                period,
                custom_days=custom_days,
                custom_start=custom_start,
                custom_end=custom_end
            )

            if not is_earnings_period:
                asset_returns = period_returns[ticker].to_numpy(dtype=float)
                bm_returns = period_returns[bm].to_numpy(dtype=float)
                joint_mask = np.isfinite(asset_returns) & np.isfinite(bm_returns)
                observations = int(np.sum(joint_mask))
            else:
                target_dates = ticker_reaction_dates.get(ticker) or ticker_reaction_dates.get(bm) or all_reac_dates_union
                if target_dates and ticker != bm:
                    r_t = returns.loc[target_dates, ticker].to_numpy(dtype=float)
                    r_b = returns.loc[target_dates, bm].to_numpy(dtype=float)
                    joint_mask = np.isfinite(r_t) & np.isfinite(r_b)
                    observations = int(np.sum(joint_mask))
                    asset_returns = r_t
                    bm_returns = r_b
                else:
                    observations = h_info.get("history_observations", len(returns))
                    asset_returns = np.array([])
                    bm_returns = np.array([])
                    joint_mask = np.array([], dtype=bool)

            b_val_float = 1.0 if ticker == bm else (bm_beta.get(ticker) if bm_beta.get(ticker) is not None else np.nan)
            vol_val = std_series.get(ticker)
            vol_float = float(vol_val) if (vol_val is not None and np.isfinite(vol_val)) else None

            if observations < 2 or b_val_float is None or not np.isfinite(b_val_float):
                expected_range_bm[ticker] = {
                    "beta": 1.0 if ticker == bm else None,
                    "actual_move": None,
                    "expected_move": None,
                    "expected_stock_move": None,
                    "expected_return": None,
                    "difference": None,
                    "normalized_move": None,
                    "normalized_difference": None,
                    "volatility": vol_float,
                    "residual_volatility": None,
                    "benchmark_move": float(bm_current_move),
                    "observations": observations,
                    "is_partial_history": bool(h_info.get("is_partial", False)),
                    "history_period_ru": h_info.get("history_period_ru"),
                    "history_text": h_info.get("history_text"),
                    "history_start": h_info.get("history_start"),
                    "history_end": h_info.get("history_end"),
                    "history_days": h_info.get("history_days"),
                    "history_observations": h_info.get("history_observations", observations),
                    "selected_period": period,
                    "selected_period_label": get_period_label_ru(period),
                }
                continue

            # Expected Move
            if np.isfinite(b_val_float):
                exp_move = bm_current_move * b_val_float
            else:
                exp_move = np.nan

            # Actual Move
            if ticker == bm:
                act_move = bm_current_move
            else:
                act_move = cleaned_asset_moves.get(ticker, np.nan)

            # Difference
            if np.isfinite(act_move) and np.isfinite(exp_move):
                diff = act_move - exp_move
            else:
                diff = np.nan

            # Normalized Move
            if np.isfinite(act_move) and np.isfinite(b_val_float) and b_val_float != 0:
                norm_move = act_move / b_val_float
            else:
                norm_move = np.nan

            # Normalized Difference (sigma units)
            if np.isfinite(diff) and vol_float is not None and np.isfinite(vol_float) and vol_float > 0:
                norm_diff = diff / vol_float
            else:
                norm_diff = np.nan

            # Residual Volatility
            if np.isfinite(b_val_float) and len(asset_returns) > 0 and len(bm_returns) > 0 and observations >= 2:
                res = asset_returns[joint_mask] - (b_val_float * bm_returns[joint_mask])
                res_vol = float(np.std(res, ddof=1)) if len(res) >= 2 else None
            else:
                res_vol = None

            expected_range_bm[ticker] = {
                "beta": b_val_float if np.isfinite(b_val_float) else None,
                "actual_move": float(act_move) if np.isfinite(act_move) else None,
                "expected_move": float(exp_move) if np.isfinite(exp_move) else None,
                "expected_stock_move": float(exp_move) if np.isfinite(exp_move) else None,
                "expected_return": float(exp_move) if np.isfinite(exp_move) else None,
                "difference": float(diff) if np.isfinite(diff) else None,
                "normalized_move": float(norm_move) if np.isfinite(norm_move) else None,
                "normalized_difference": float(norm_diff) if np.isfinite(norm_diff) else None,
                "volatility": vol_float,
                "residual_volatility": res_vol if (res_vol is not None and np.isfinite(res_vol)) else None,
                "benchmark_move": float(bm_current_move),
                "observations": observations,
                "is_partial_history": bool(h_info.get("is_partial", False)),
                "history_period_ru": h_info.get("history_period_ru"),
                "history_text": h_info.get("history_text"),
                "history_start": h_info.get("history_start"),
                "history_end": h_info.get("history_end"),
                "history_days": h_info.get("history_days"),
                "history_observations": h_info.get("history_observations", observations),
                "selected_period": period,
                "selected_period_label": period_label_ru,
            }

        # Group Summaries for this benchmark
        group_summary_bm = {}
        for group_name, tickers in groups.items():
            betas = []
            correlations = []
            valid_assets = 0

            for t in tickers:
                t = str(t).strip().upper()
                if t not in symbols:
                    continue
                valid_assets += 1
                b = bm_beta.get(t)
                c = correlation.get(t, {}).get(bm)

                if b is not None and np.isfinite(b):
                    betas.append(b)
                if c is not None and np.isfinite(c):
                    correlations.append(c)

            group_summary_bm[group_name] = {
                "average_beta": float(np.mean(betas)) if betas else None,
                "average_correlation": float(np.mean(correlations)) if correlations else None,
                "assets": valid_assets,
                "total_configured": len(tickers),
            }

        benchmarks_data[bm] = {
            "benchmark_beta": bm_beta,
            "expected_range": expected_range_bm,
            "group_summary": group_summary_bm,
            "benchmark_move": float(bm_current_move),
        }

    # Primary benchmark data for backward compatibility
    primary_data = benchmarks_data[primary_benchmark]

    return {
        "period": period,
        "period_label_ru": period_label_ru,
        "metric": metric_clean,
        "metric_label_ru": metric_label_ru,
        "session_mode": session_mode_clean if metric_clean == "session" else None,
        "period_start": period_returns.index.min().strftime("%Y-%m-%d"),
        "period_end": period_returns.index.max().strftime("%Y-%m-%d"),
        "correlation": correlation,
        "beta": beta,
        "benchmarks": clean_benchmarks,
        "primary_benchmark": primary_benchmark,
        "benchmark": primary_benchmark,
        "reference": primary_benchmark,
        "benchmark_moves": cleaned_benchmark_moves,
        "benchmark_move": float(cleaned_benchmark_moves.get(primary_benchmark, benchmark_move)),
        "benchmark_beta": primary_data["benchmark_beta"],
        "expected_range": primary_data["expected_range"],
        "group_summary": primary_data["group_summary"],
        "benchmarks_data": benchmarks_data,
        "tickers": [t for t in symbols if t not in clean_benchmarks],
        "all_symbols": symbols,
        "ignored_tickers": ignored_tickers,
        "partial_tickers": partial_tickers,
        "tickers_history_info": tickers_history_info,
        "anomaly_mode": anomaly_mode_clean,
        "anomaly_metric": anomaly_metric_clean if anomaly_mode_clean != "none" else None,
        "anomaly_op": anomaly_op_clean if anomaly_mode_clean != "none" else None,
        "anomaly_threshold": thresh if anomaly_mode_clean != "none" else None,
        "anomaly_target": anomaly_target_clean if anomaly_mode_clean != "none" else None,
        "anomaly_label_ru": anomaly_label_ru,
        "anomaly_days_count": anomaly_days_count,
        "anomaly_dates": [d.strftime("%Y-%m-%d") for d in period_returns.index] if anomaly_mode_clean != "none" else None,
    }


# ============================================================
# COMPATIBILITY WRAPPER
# ============================================================

def calculate_all_periods(
    df,
    stock,
    benchmark,
    benchmark_move=0.01,
    asset_move=None,
):
    results = {}
    stock = str(stock).strip().upper()

    for period in PERIODS:
        try:
            analysis = calculate_groups_analysis(
                df,
                {"Analysis": [stock]},
                benchmark=benchmark,
                period=period,
                benchmark_move=benchmark_move,
                asset_moves={stock: asset_move} if asset_move is not None else {},
            )

            # If stock was ignored due to data history < period duration
            if stock in analysis.get("ignored_tickers", {}) or stock not in analysis.get("tickers", []):
                results[period] = None
                continue

            item = analysis["expected_range"].get(stock, {})
            if not item or item.get("beta") is None:
                results[period] = None
                continue

            results[period] = {
                "correlation": analysis["correlation"].get(stock, {}).get(str(benchmark).upper()),
                "beta": item.get("beta"),
                "actual_move": item.get("actual_move"),
                "expected_move": item.get("expected_stock_move") if item.get("expected_stock_move") is not None else item.get("expected_move"),
                "expected_return": item.get("expected_return"),
                "expected_stock_move": item.get("expected_stock_move"),
                "difference": item.get("difference"),
                "normalized_move": item.get("normalized_move"),
                "normalized_difference": item.get("normalized_difference"),
                "volatility": item.get("volatility"),
                "benchmark_move": item.get("benchmark_move", benchmark_move),
                "observations": item.get("observations", 0),
                "start_date": analysis["period_start"],
                "end_date": analysis["period_end"],
                "is_partial_history": item.get("is_partial_history", False),
                "history_period_ru": item.get("history_period_ru"),
                "history_text": item.get("history_text"),
                "history_start": item.get("history_start"),
                "history_end": item.get("history_end"),
                "history_days": item.get("history_days"),
                "history_observations": item.get("history_observations"),
                "selected_period": period,
                "selected_period_label": get_period_label_ru(period),
            }
        except Exception as e:
            results[period] = None

    return results, pd.DataFrame()