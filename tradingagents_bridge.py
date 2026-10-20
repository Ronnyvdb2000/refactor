#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
tradingagents_bridge.py — TRADINGAGENTS KOPPELING v2.0

Refactor: gebruikt nu common/ voor alle gedeelde bouwstenen.
Leest vcp_signals.json en stuurt de tickers door naar de TradingAgents API.
"""

import os
import sys
import json
import requests
from typing import List, Dict, Optional

from common import (
    today_str, safe_float, esc,
    send_telegram, send_email
)

# ============================================================
# CONFIG
# ============================================================

VCP_SIGNALS_PATH = "vcp_signals.json"
TRADINGAGENTS_API_URL = os.getenv("TRADINGAGENTS_API_URL", "http://localhost:8000/api/analyze")
TRADINGAGENTS_API_KEY = os.getenv("TRADINGAGENTS_API_KEY", "")

# ============================================================
# HULPFUNCTIES
# ============================================================

def laad_vcp_signals() -> List[str]:
    if not os.path.exists(VCP_SIGNALS_PATH):
        print(f"[WARN] {VCP_SIGNALS_PATH} niet gevonden.")
        return []
    try:
        with open(VCP_SIGNALS_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        tickers = data.get("tickers", [])
        print(f"{len(tickers)} tickers geladen uit {VCP_SIGNALS_PATH}.")
        return tickers
    except Exception as e:
        print(f"[WARN] Kon {VCP_SIGNALS_PATH} niet lezen: {e}")
        return []

def stuur_naar_tradingagents(tickers: List[str]) -> Optional[Dict]:
    if not tickers:
        return None

    headers = {"Content-Type": "application/json"}
    if TRADINGAGENTS_API_KEY:
        headers["Authorization"] = f"Bearer {TRADINGAGENTS_API_KEY}"

    payload = {"tickers": tickers, "datum": today_str()}

    try:
        resp = requests.post(TRADINGAGENTS_API_URL, json=payload, headers=headers, timeout=30)
        if resp.status_code == 200:
            print(f"Succesvol verstuurd naar TradingAgents: {len(tickers)} tickers.")
            return resp.json()
        else:
            print(f"[WARN] TradingAgents API fout: {resp.status_code} {resp.text[:200]}")
            return None
    except Exception as e:
        print(f"[WARN] Kon TradingAgents API niet bereiken: {e}")
        return None

# ============================================================
# MAIN
# ============================================================

def main():
    print(f"{'='*60}\nTRADINGAGENTS BRIDGE — {today_str()}\n{'='*60}")

    tickers = laad_vcp_signals()
    if not tickers:
        print("Geen tickers om te versturen. Klaar.")
        return

    print(f"Versturen van {len(tickers)} tickers naar {TRADINGAGENTS_API_URL}...")
    resultaat = stuur_naar_tradingagents(tickers)

    if resultaat:
        # Optioneel: stuur een samenvatting naar Telegram
        samenvatting = (
            f"🤖 *TradingAgents Bridge* — {today_str()}\n"
            f"{len(tickers)} VCP-tickers verstuurd.\n"
            f"API response: `{esc(str(resultaat)[:200])}`"
        )
        send_telegram(samenvatting, parse_mode="Markdown")
    else:
        print("Verzending mislukt of geen response.")

    print(f"\n{'='*60}\nKlaar.")

if __name__ == "__main__":
    main()
