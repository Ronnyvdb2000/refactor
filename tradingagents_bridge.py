  #!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
tradingagents_bridge.py

Koppelt TradingAgents (multi-agent LLM-analyse) aan een signaalbron.

Twee bronnen worden ondersteund:
  --source vcp  (default): leest vcp_signals.json (output van bot_00vcp)
  --source par          : leest results/a_par_model_{horizon}_picks.csv

Werking:
  1. Bron genereert kandidaat-tickers
  2. Dit script leest de tickers, laat TradingAgents per ticker een besluit
     genereren, en stuurt het resultaat naar Telegram en/of Gmail.

Blijft gratis door:
  - Groq als LLM-provider (gratis rate-limited tier, geen lokale GPU nodig,
    en werkt dus prima binnen de gratis GitHub Actions-runner)
  - max_debate_rounds=1 om binnen de gratis Groq rate limits te blijven
  - online_tools=False om prompts klein te houden (anders 413-fouten)
  - GitHub Actions blijft gratis voor public/private repos binnen de
    standaard minuten-quota; dit stapje kost enkele API-calls, geen compute

Benodigde secrets:
  GROQ_API_KEY   -> gratis aan te maken op https://console.groq.com
  TELEGRAM_TOKEN, TELEGRAM_CHAT_ID  (voor --channel telegram/both)
  EMAIL_USER, EMAIL_PASS, EMAIL_RECEIVER  (voor --channel email/both)
"""

import os
import sys
import csv
import json
import time
import argparse
import smtplib
import datetime
from email.mime.text import MIMEText

import requests

from tradingagents.graph.trading_graph import TradingAgentsGraph
from tradingagents.default_config import DEFAULT_CONFIG


# ============================================================
# CONFIG
# ============================================================

def build_config():
    """Gratis configuratie: Groq-inference i.p.v. betaalde OpenAI/Anthropic calls."""
    config = DEFAULT_CONFIG.copy()
    config["llm_provider"] = "groq"
    config["backend_url"] = "https://api.groq.com/openai/v1"
    # Beide modellen zijn gratis-tier Groq-modellen
    # llama-3.3-70b-versatile heeft een hogere gratis TPM-limiet (12.000)
    # dan llama-3.1-8b-instant (6.000).
    config["deep_think_llm"] = "llama-3.3-70b-versatile"
    config["quick_think_llm"] = "llama-3.3-70b-versatile"
    # Kleinere debat-diepte = minder calls = ruim binnen gratis rate limits
    config["max_debate_rounds"] = 1
    config["max_risk_discuss_rounds"] = 1
    # Uitgezet: online_tools haalt live nieuws/social data op en maakt de
    # prompt vaak 10-60k tokens groot — dat past niet binnen de gratis
    # Groq TPM-limiet.
    config["online_tools"] = False
    return config


def analyze_ticker(ticker: str, trade_date: str) -> str:
    config = build_config()
    ta = TradingAgentsGraph(debug=False, config=config)
    # Eén retry bij een tijdelijke 429 rate-limit (i.t.t. 413, dat is
    # structureel te groot en lost niet op door te wachten).
    for attempt in range(2):
        try:
            _, decision = ta.propagate(ticker, trade_date)
            return decision
        except Exception as e:
            msg = str(e)
            if "429" in msg and attempt == 0:
                print(f"  {ticker}: tijdelijke rate limit, 10s wachten en opnieuw...")
                time.sleep(10)
                continue
            raise


# ============================================================
# BRONNEN
# ============================================================

def lees_vcp_signals(pad: str) -> list:
    """Leest vcp_signals.json (output van bot_00vcp)."""
    if not os.path.exists(pad):
        print(f"Geen signalenbestand gevonden: {pad}")
        return []
    with open(pad) as f:
        signals = json.load(f)
    # Verwacht formaat: {"tickers": ["ABI.BR", "KBC.BR"]}
    return signals.get("tickers", [])


def lees_par_picks(horizon: str, top_n: int) -> list:
    """Leest picks uit results/a_par_model_{horizon}_picks.csv."""
    pad = f"results/a_par_model_{horizon}_picks.csv"
    if not os.path.exists(pad):
        print(f"Geen par-picks gevonden: {pad}")
        return []
    try:
        with open(pad, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            rijen = list(reader)
        tickers = [r["ticker"] for r in rijen if r.get("ticker")][:top_n]
        print(f"{len(tickers)} tickers gelezen uit {pad}")
        return tickers
    except Exception as e:
        print(f"Fout bij lezen {pad}: {e}")
        return []


# ============================================================
# NOTIFICATIES
# ============================================================

def send_telegram(message: str) -> None:
    token = os.environ["TELEGRAM_TOKEN"]
    chat_id = os.environ["TELEGRAM_CHAT_ID"]
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    # Telegram limiteert tot 4096 tekens per bericht — knip op in stukken.
    for i in range(0, len(message), 4096):
        chunk = message[i:i + 4096]
        try:
            resp = requests.post(
                url,
                data={"chat_id": chat_id, "text": chunk, "parse_mode": "HTML",
                      "disable_web_page_preview": True},
                timeout=30,
            )
            resp.raise_for_status()
        except Exception as e:
            # Fallback zonder parse_mode: voorkomt 400-fouten door tekens
            # als < of > die Telegram als (kapotte) HTML-tags interpreteert.
            try:
                requests.post(
                    url,
                    data={"chat_id": chat_id, "text": chunk,
                          "disable_web_page_preview": True},
                    timeout=30,
                )
                print(f"Telegram HTML-fout, verstuurd zonder opmaak: {e}")
            except Exception as e2:
                print(f"Telegram fout: {e2}")
        if i + 4096 < len(message):
            time.sleep(1)


def send_email(subject: str, message: str) -> None:
    user = os.environ["EMAIL_USER"]
    password = os.environ["EMAIL_PASS"]
    receiver = os.environ["EMAIL_RECEIVER"]

    msg = MIMEText(message)
    msg["Subject"] = subject
    msg["From"] = user
    msg["To"] = receiver

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(user, password)
        server.send_message(msg)


# ============================================================
# MAIN
# ============================================================

def main():
    parser = argparse.ArgumentParser(description="TradingAgents-bridge")
    parser.add_argument(
        "--source",
        choices=["vcp", "par"],
        default="vcp",
        help="vcp = bot_00vcp signalen uit vcp_signals.json; "
             "par = a_par_model picks uit results/a_par_model_{horizon}_picks.csv "
             "(default: vcp)",
    )
    parser.add_argument(
        "--signals-file",
        default="vcp_signals.json",
        help="JSON-bestand met VCP-tickers (alleen bij --source vcp)",
    )
    parser.add_argument(
        "--horizon",
        default="10d",
        help="Horizon voor par-picks: 5d/10d/20d/30d/60d (default: 10d)",
    )
    parser.add_argument(
        "--top-n",
        type=int,
        default=10,
        help="Max aantal par-picks om te analyseren (default: 10)",
    )
    parser.add_argument(
        "--date",
        default=None,
        help="Handelsdatum YYYY-MM-DD (default: vandaag)",
    )
    parser.add_argument(
        "--channel",
        choices=["telegram", "email", "both"],
        default="telegram",
        help="Waarheen het resultaat sturen (default: telegram)",
    )
    args = parser.parse_args()

    trade_date = args.date or datetime.date.today().isoformat()

    # Bepaal bron
    if args.source == "vcp":
        tickers = lees_vcp_signals(args.signals_file)
        bron_label = "VCP-kandidaten"
        if not tickers:
            print("Geen VCP-kandidaten vandaag — TradingAgents wordt overgeslagen.")
            return
    else:  # par
        tickers = lees_par_picks(args.horizon, args.top_n)
        bron_label = f"a_par_model top {args.top_n} ({args.horizon})"
        if not tickers:
            print(f"Geen par-picks beschikbaar ({bron_label}) — TradingAgents wordt overgeslagen.")
            return

    print(f"Bron: {bron_label}")
    print(f"Tickers: {', '.join(tickers)}")
    print(f"Datum: {trade_date}")
    print("")

    # Analyse per ticker
    blocks = []
    for ticker in tickers:
        print(f"Analyseren: {ticker} ...")
        try:
            decision = analyze_ticker(ticker, trade_date)
            blocks.append(f"📊 <b>{ticker}</b>\n{decision}\n")
        except Exception as e:
            blocks.append(f"⚠️ {ticker}: analyse mislukt ({e})")
        # Kleine pauze tussen tickers om de gratis TPM-limiet niet te bursten
        time.sleep(5)

    message = f"TradingAgents-analyse — {bron_label} ({trade_date}):\n\n" + "\n".join(blocks)

    # Versturen
    if args.channel in ("telegram", "both"):
        send_telegram(message)
        print("Verstuurd naar Telegram.")
    if args.channel in ("email", "both"):
        send_email(f"TradingAgents {bron_label} {trade_date}", message)
        print("Verstuurd naar Gmail.")


if __name__ == "__main__":
    main()
