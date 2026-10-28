import os
import sys
import datetime
from typing import Dict, List, Any, Optional, Tuple
import numpy as np
import pandas as pd
import psycopg
from psycopg.rows import dict_row

from config import DB_CONFIG, MARKET_TIMEZONE

# ============================================================
# STATISTICAL ANOMALY & PRE-MARKET TIMING ENGINE
# ============================================================

# Color definitions according to user specification:
# 1. 0.3 - 0.5 sigma: Translucent Green (60% transparent / alpha 0.40)
# 2. 0.5 - 0.8 sigma: Yellow
# 3. ~1.0 sigma (0.8 - 1.2): Vibrant Green
# 4. > 1.0 sigma (> 1.2): Orange
COLOR_MAP = {
    "none": {
        "label": "Normal",
        "bg_hex": "#ffffff",
        "bg_rgba": "rgba(255, 255, 255, 0)",
        "text_color": "#2c3e50",
        "excel_fill": None
    },
    "anomaly_light_green": {
        "label": "0.3 - 0.5 sigma (Strange Move)",
        "bg_hex": "#d4efdf",
        "bg_rgba": "rgba(46, 204, 113, 0.40)",
        "text_color": "#145a32",
        "excel_fill": "D4EFDF"
    },
    "anomaly_yellow": {
        "label": "0.5 - 0.8 sigma (Noticeable)",
        "bg_hex": "#fcf3cf",
        "bg_rgba": "rgba(241, 196, 15, 0.85)",
        "text_color": "#7d6608",
        "excel_fill": "FCF3CF"
    },
    "anomaly_green": {
        "label": "~1.0 sigma (Standard Gap)",
        "bg_hex": "#27ae60",
        "bg_rgba": "rgba(39, 174, 96, 0.90)",
        "text_color": "#ffffff",
        "excel_fill": "27AE60"
    },
    "anomaly_orange": {
        "label": "> 1.0 sigma (Extreme Gap)",
        "bg_hex": "#e67e22",
        "bg_rgba": "rgba(230, 126, 34, 0.95)",
        "text_color": "#ffffff",
        "excel_fill": "E67E22"
    }
}

class TimingAnomalyEngine:
    def __init__(self, days_back: int = 90):
        self.days_back = days_back
        # In-memory cache for profiles: ticker -> time_str -> stats
        self._profiles_cache: Dict[str, Dict[str, Dict[str, float]]] = {}
        self._prev_close_cache: Dict[str, float] = {}
        self._last_ticks: Dict[str, Dict[str, Any]] = {}

    def get_postgres_connection(self):
        cfg = dict(DB_CONFIG)
        cfg["sslmode"] = "disable"
        return psycopg.connect(**cfg, connect_timeout=5)

    def load_historical_time_profile(self, ticker: str, sync_db: bool = False) -> Dict[str, Dict[str, float]]:
        """
        Builds historical Time-of-Day baseline distribution for `ticker`.
        Returns mapping: time_str ('HH:MM') -> { 'mean_abs_move', 'std_abs_move', ... }
        Fast non-blocking lookup: returns pre-calculated or analytical profile immediately.
        """
        ticker = str(ticker).strip().upper()
        if ticker in self._profiles_cache:
            return self._profiles_cache[ticker]

        if not sync_db:
            return self._generate_default_profile(ticker)

        try:
            with self.get_postgres_connection() as conn:
                with conn.cursor(row_factory=dict_row) as cur:
                    # 1. Get daily prev_close for each day
                    sql_daily = """
                        SELECT d.date::text as date, d.prev_close::float, d.close::float
                        FROM daily d
                        JOIN ticker t ON t.id = d.id_ticker
                        WHERE UPPER(t.ticker) = %s AND d.date >= %s
                        ORDER BY d.date ASC;
                    """
                    cur.execute(sql_daily, (ticker, start_date))
                    daily_rows = cur.fetchall()
                    if not daily_rows:
                        return self._generate_default_profile(ticker)

                    prev_close_map = {}
                    for row in daily_rows:
                        pc = row["prev_close"] or row["close"]
                        if pc and pc > 0:
                            prev_close_map[row["date"]] = pc

                    if daily_rows:
                        # Cache latest known close
                        self._prev_close_cache[ticker] = daily_rows[-1]["close"] or daily_rows[-1]["prev_close"] or 100.0

                    # 2. Get 1-minute intraday bars
                    sql_intra = """
                        SELECT 
                            i.date::text as date,
                            to_char(i.time, 'HH24:MI') as time_str,
                            i.close::float as close
                        FROM intraday_chart i
                        JOIN ticker t ON t.id = i.id_ticker
                        WHERE UPPER(t.ticker) = %s 
                          AND i.date >= %s
                        ORDER BY i.date ASC, i.time ASC;
                    """
                    cur.execute(sql_intra, (ticker, start_date))
                    intra_rows = cur.fetchall()

                    if not intra_rows:
                        return self._generate_default_profile(ticker)

                    df = pd.DataFrame(intra_rows)
                    df["prev_close"] = df["date"].map(prev_close_map)
                    df = df.dropna(subset=["prev_close"])
                    df = df[df["prev_close"] > 0]

                    # % move from previous close
                    df["pct_move"] = ((df["close"] - df["prev_close"]) / df["prev_close"]) * 100.0
                    df["abs_pct_move"] = df["pct_move"].abs()

                    # Velocity: 5-minute rate of change
                    df["velocity"] = df.groupby("date")["pct_move"].diff(5).abs() / 5.0

                    # Group by time_str (e.g. 04:00, 05:00, 08:30, 09:30)
                    profile = {}
                    for time_str, grp in df.groupby("time_str"):
                        moves = grp["abs_pct_move"].values
                        vels = grp["velocity"].dropna().values

                        if len(moves) >= 3:
                            mean_m = float(np.mean(moves))
                            std_m = float(np.std(moves, ddof=1)) if len(moves) > 1 else 0.15
                            if std_m < 0.05:
                                std_m = 0.05

                            mean_v = float(np.mean(vels)) if len(vels) > 0 else 0.01
                            std_v = float(np.std(vels, ddof=1)) if len(vels) > 1 else 0.02
                            if std_v < 0.01:
                                std_v = 0.01

                            profile[time_str] = {
                                "mean_abs_move": round(mean_m, 4),
                                "std_abs_move": round(std_m, 4),
                                "mean_velocity": round(mean_v, 4),
                                "std_velocity": round(std_v, 4),
                                "sample_days": int(len(moves))
                            }

                    if profile:
                        self._profiles_cache[ticker] = profile
                        return profile

        except Exception as e:
            print(f"Error loading profile for {ticker} from DB: {e}")

        return self._generate_default_profile(ticker)

    def _generate_default_profile(self, ticker: str) -> Dict[str, Dict[str, float]]:
        """
        Fallback analytical profile based on empirical US equities market curve:
        - 04:00 - 06:00: mean move 0.10%, sigma 0.12%
        - 06:00 - 08:00: mean move 0.25%, sigma 0.25%
        - 08:00 - 09:20: mean move 0.50%, sigma 0.45%
        - 09:30+: mean move 0.80%, sigma 0.70%
        """
        profile = {}
        for h in range(4, 20):
            for m in range(0, 60, 5):
                t_str = f"{h:02d}:{m:02d}"
                minutes_from_open = (h - 9) * 60 + m - 30

                if h < 6:
                    mean_m, std_m, vel = 0.10, 0.12, 0.015
                elif h < 8:
                    mean_m, std_m, vel = 0.25, 0.22, 0.025
                elif h == 8 or (h == 9 and m <= 20):
                    mean_m, std_m, vel = 0.50, 0.40, 0.05
                else:
                    mean_m, std_m, vel = 0.75, 0.65, 0.08

                profile[t_str] = {
                    "mean_abs_move": mean_m,
                    "std_abs_move": std_m,
                    "mean_velocity": vel,
                    "std_velocity": vel * 1.5,
                    "sample_days": 30
                }
        self._profiles_cache[ticker] = profile
        return profile

    def classify_anomaly(
        self,
        ticker: str,
        current_price: float,
        prev_close: Optional[float] = None,
        time_str: Optional[str] = None,
        speed_5m_pct: Optional[float] = None
    ) -> Dict[str, Any]:
        """
        Computes the Z-Score and determines anomaly color category based on:
        1. Time-of-day baseline (e.g. at 05:00 AM)
        2. Gap velocity (speed of move)
        3. Deviation from historical mean
        """
        ticker = str(ticker).strip().upper()
        if not prev_close:
            prev_close = self._prev_close_cache.get(ticker, current_price)

        if not prev_close or prev_close <= 0:
            pct_move = 0.0
        else:
            pct_move = ((current_price - prev_close) / prev_close) * 100.0

        abs_pct_move = abs(pct_move)

        # Determine time bucket (default to current ET time)
        if not time_str:
            now_utc = datetime.datetime.now(datetime.timezone.utc)
            # US Eastern is UTC-4 (EDT) or UTC-5 (EST)
            et_hour = (now_utc.hour - 4) % 24
            time_str = f"{et_hour:02d}:{now_utc.minute:02d}"

        # Match nearest 5-minute bucket
        h, m = [int(x) for x in time_str.split(":")[:2]]
        m_bucket = (m // 5) * 5
        bucket_key = f"{h:02d}:{m_bucket:02d}"

        profile = self.load_historical_time_profile(ticker)
        baseline = profile.get(bucket_key)
        if not baseline:
            # Fallback to nearest available key
            keys = sorted(profile.keys())
            baseline = profile[keys[0]] if keys else {"mean_abs_move": 0.25, "std_abs_move": 0.3, "mean_velocity": 0.02, "std_velocity": 0.03}

        mean_move = baseline["mean_abs_move"]
        std_move = max(0.05, baseline["std_abs_move"])

        # 1. Price level Sigma (Z-Score)
        # Note: (current_move - mean_move) / std
        raw_z_level = (abs_pct_move - mean_move) / std_move

        # 2. Velocity Sigma (Speed of move)
        z_velocity = 0.0
        if speed_5m_pct is not None and speed_5m_pct > 0:
            mean_vel = baseline.get("mean_velocity", 0.02)
            std_vel = max(0.01, baseline.get("std_velocity", 0.03))
            z_velocity = (speed_5m_pct - mean_vel) / std_vel

        # Composite Sigma:
        # Incorporates velocity bonus if stock gap happened rapidly
        velocity_boost = max(0.0, z_velocity * 0.25)
        total_sigma = max(0.0, raw_z_level + velocity_boost)

        # 3. Apply User Color Criteria:
        # - 0.3 - 0.5 sigma: green (60% transparent)
        # - 0.5 - 0.8 sigma: yellow
        # - ~1.0 sigma (0.8 - 1.2): green
        # - > 1.0 sigma (> 1.2): orange
        if total_sigma >= 1.2:
            color_category = "anomaly_orange"
        elif total_sigma >= 0.8:
            color_category = "anomaly_green"
        elif total_sigma >= 0.5:
            color_category = "anomaly_yellow"
        elif total_sigma >= 0.3:
            color_category = "anomaly_light_green"
        else:
            color_category = "none"

        style = COLOR_MAP[color_category]

        return {
            "ticker": ticker,
            "price": round(current_price, 2),
            "prev_close": round(prev_close, 2),
            "pct_move": round(pct_move, 2),
            "abs_pct_move": round(abs_pct_move, 2),
            "time_str": time_str,
            "baseline_mean": round(mean_move, 3),
            "baseline_std": round(std_move, 3),
            "sigma": round(total_sigma, 2),
            "raw_z_level": round(raw_z_level, 2),
            "z_velocity": round(z_velocity, 2),
            "color_category": color_category,
            "color_label": style["label"],
            "bg_hex": style["bg_hex"],
            "bg_rgba": style["bg_rgba"],
            "text_color": style["text_color"],
            "excel_fill": style["excel_fill"]
        }

# Global engine instance
anomaly_engine = TimingAnomalyEngine(days_back=90)
