import sys
import os
import time
import json
import threading
from typing import Dict, List, Any, Optional, Callable
import datetime

# ============================================================
# ESIGNAL REAL-TIME DATA COLLECTOR & BRIDGES
# ============================================================

class ESignalBridge:
    """
    Unified manager for streaming real-time quotes from eSignal.
    Supports 3 production modes:
    1. EXCEL_RTD: Reads live streaming RTD values from Excel connected to eSignal.
    2. EFS_WEBHOOK: Receives live ticks sent directly by eSignal Formula Script (EFS).
    3. COM_ACTIVEX: Connects directly via Windows COM to eSignal Desktop API.
    """

    def __init__(self, mode: str = "EXCEL_RTD"):
        self.mode = mode.upper()
        self.live_quotes: Dict[str, Dict[str, Any]] = {}
        self._subscribers: List[Callable[[Dict[str, Any]], None]] = []
        self._running = False
        self._thread: Optional[threading.Thread] = None

    def register_callback(self, callback: Callable[[Dict[str, Any]], None]):
        """Registers a function to be notified on new ticks."""
        self._subscribers.append(callback)

    def notify_subscribers(self, quote: Dict[str, Any]):
        for cb in self._subscribers:
            try:
                cb(quote)
            except Exception as e:
                print(f"Callback error in ESignalBridge: {e}")

    # ------------------------------------------------------------
    # 1. EXCEL RTD BRIDGE (win32com to active Excel with eSignal RTD)
    # ------------------------------------------------------------
    def start_excel_rtd_listener(
        self,
        workbook_name: str = "analysis.xlsm",
        sheet_name: str = "Resulting_table",
        poll_interval_sec: float = 0.5
    ):
        """
        Polls the running Excel workbook where eSignal feeds RTD quotes.
        eSignal RTD formulas look like: =RTD("esignal.rtd", , "MCD", "Last")
        """
        try:
            import win32com.client
        except ImportError:
            print("pywin32 is required for Excel RTD listener. Run: pip install pywin32")
            return

        def _worker():
            print(f"[ESignal RTD] Connecting to Excel.Application (Workbook: {workbook_name})...")
            try:
                excel = win32com.client.Dispatch("Excel.Application")
            except Exception as e:
                print(f"[ESignal RTD] Could not connect to Excel: {e}")
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
                print(f"[ESignal RTD] Error finding workbook: {e}")
                return

            if not wb:
                print("[ESignal RTD] No active workbook found.")
                return

            try:
                sheet = wb.Sheets(sheet_name)
            except Exception:
                sheet = wb.ActiveSheet

            print(f"[ESignal RTD] Connected successfully to sheet: {sheet.Name}")
            self._running = True

            while self._running:
                try:
                    # In user's table, columns are groups of 3:
                    # Group 1: Col 2 (Move), 3 (Ticker), 4 (Price)
                    # Group 2: Col 6 (Move), 7 (Ticker), 8 (Price)
                    # and so on for col_offset in [2, 6, 10, 14, 18, 22, 26, 30, 34]
                    col_offsets = [2, 6, 10, 14, 18, 22, 26, 30, 34]
                    now_str = datetime.datetime.now().strftime("%H:%M:%S")

                    for col in col_offsets:
                        for row in range(2, 68):
                            ticker = sheet.Cells(row, col + 1).Value
                            if not ticker:
                                continue
                            ticker = str(ticker).strip().upper()
                            if ticker in ["ETFS", "MAIN_DATA", ""]:
                                continue

                            move_val = sheet.Cells(row, col).Value
                            price_val = sheet.Cells(row, col + 2).Value

                            try:
                                p = float(price_val) if price_val is not None else 0.0
                                m = float(move_val) if move_val is not None else 0.0
                            except (ValueError, TypeError):
                                continue

                            quote = {
                                "ticker": ticker,
                                "price": p,
                                "pct_move": m,
                                "time": now_str,
                                "source": "eSignal_RTD"
                            }
                            self.live_quotes[ticker] = quote
                            self.notify_subscribers(quote)

                except Exception as e:
                    print(f"[ESignal RTD] Read error: {e}")

                time.sleep(poll_interval_sec)

        self._thread = threading.Thread(target=_worker, daemon=True)
        self._thread.start()

    # ------------------------------------------------------------
    # 2. ESIGNAL EFS RECEIVER (Direct push from eSignal via HTTP)
    # ------------------------------------------------------------
    def on_efs_tick(self, data: Dict[str, Any]):
        """
        Processes a live quote payload pushed by eSignal EFS script:
        {
            "ticker": "MCD",
            "last": 238.9,
            "change_pct": 0.50,
            "volume": 125000,
            "time": "05:00:12"
        }
        """
        ticker = str(data.get("ticker", "")).strip().upper()
        if not ticker:
            return

        quote = {
            "ticker": ticker,
            "price": float(data.get("last", 0.0)),
            "pct_move": float(data.get("change_pct", 0.0)),
            "volume": float(data.get("volume", 0.0)),
            "time": data.get("time", datetime.datetime.now().strftime("%H:%M:%S")),
            "source": "eSignal_EFS"
        }
        self.live_quotes[ticker] = quote
        self.notify_subscribers(quote)

    # ------------------------------------------------------------
    # 3. ESIGNAL ACTIVEX / COM CLIENT (Native IESignal.Hooks)
    # ------------------------------------------------------------
    def build_com_client_template(self) -> str:
        """
        Returns full standalone Python code template for eSignal Desktop COM API.
        """
        return '''# Standalone eSignal COM API client
import win32com.client
import pythoncom
import time

class ESignalEvents:
    def OnQuoteChanged(self, symbol, update_type):
        # Triggered whenever symbol quote changes
        print(f"[COM Tick] Symbol: {symbol}, Type: {update_type}")

    def OnBarsReceived(self, symbol, bar_count):
        print(f"[COM Bars] Symbol: {symbol}, Count: {bar_count}")

def run_esignal_com():
    print("Connecting to eSignal ActiveX COM Server...")
    try:
        esignal = win32com.client.DispatchWithEvents("IESignal.Hooks", ESignalEvents)
        print("Connected! Requesting symbol quotes...")
        esignal.RequestSymbol("MCD")
        esignal.RequestSymbol("AKAM")
        esignal.RequestSymbol("ARM")

        while True:
            pythoncom.PumpWaitingMessages()
            time.sleep(0.05)
    except Exception as e:
        print("COM Connection error:", e)

if __name__ == "__main__":
    run_esignal_com()
'''

    def stop(self):
        self._running = False

# Global bridge instance
esignal_bridge = ESignalBridge()
