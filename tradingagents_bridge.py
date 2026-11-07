#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
tradingagents_bridge.py

Koppelt TradingAgents (multi-agent LLM-analyse) aan een signaalbron.

Bronnen:
  --source vcp  (default): leest vcp_signals.json (output van bot_00vcp)
  --source par          : leest results/a_par_model_{horizon}_picks.csv

Werking:
  1. Bron genereert kandidaat-tickers
  2. Dit script leest de tickers, laat TradingAgents per ticker een besluit
     genereren, en stuurt het resultaat naar Telegram en/of Gmail.

Gratis gehouden door:
  - Groq als LLM-provider (gratis rate-limited tier, geen lokale GPU nodig)
  - max_debate_rounds=1 om binnen de gratis Groq rate limits te blijven
  - online_tools=False om prompts klein te houden
  - Slimme retry-logica die Groq's TPM/TPD-foutmeldingen parseert en
    respecteert, zodat we de gratis limieten niet onnodig verbruiken
  - 413 (Request too large) wordt direct overgeslagen: geen retry zinloos

Benodigde secrets:
  GROQ_API_KEY   -> gratis aan te maken op https://console.groq.com
  TELEGRAM_TOKEN, TELEGRAM_CHAT_ID  (voor --channel telegram/both)
  EMAIL_USER, EMAIL_PASS, EMAIL_RECEIVER  (voor --channel email/both)

Optioneel:
  GROQ_MODEL     -> overschrijft het standaardmodel (zie build_config)
"""

import os
import sys
import re
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

# Standaard Groq-model.
#   llama-3.1-8b-instant     : 6k TPM, 500k TPD   <- gekozen om TPD-plafond
#   openai/gpt-oss-120b      : 8k TPM, 200k TPD   (snel TPD uitgeput)
# Let op: TPM is bij 8b-instant iets lager, daarom een langere slaap
# tussen tickers (zie --sleep). Overschrijven via GROQ_MODEL env var.
DEFAULT_GROQ_MODEL = "llama-3.1-8b-instant"

# Maximum tokens dat een enkel model-antwoord mag teruggeven. Dit verkleint
# de totale request-grootte (input + gereserveerde output) en helpt de TPM-
# limiet te respecteren.
DEFAULT_MAX_TOKENS = 2000


def build_config():
    """Gratis configuratie: Groq-inference i.p.v. betaalde OpenAI/Anthropic calls."""
    config = DEFAULT_CONFIG.copy()
    config["llm_provider"] = "groq"
    config["backend_url"] = "https://api.groq.com/openai/v1"

    model = os.environ.get("GROQ_MODEL", DEFAULT_GROQ_MODEL)
    config["deep_think_llm"] = model
    config["quick_think_llm"] = model

    # Kleinere debat-diepte = minder calls = ruim binnen gratis rate limits
    config["max_debate_rounds"] = 1
    config["max_risk_discuss_rounds"] = 1
    # Uitgezet: online_tools haalt live nieuws/social data op en maakt de
    # prompt vaak 10-60k tokens groot -> 413-fouten op de gratis tier.
    config["online_tools"] = False

    # Begrens de output-grootte per model-call (helpt tegen 429 TPM,
    # niet tegen 413: dat is een input-grootte probleem).
    max_tokens = int(os.environ.get("GROQ_MAX_TOKENS", DEFAULT_MAX_TOKENS))
    config["max_tokens"] = max_tokens

    return config


# ============================================================
# GROQ FOUT-PARSING
# ============================================================

def parse_groq_error(msg: str) -> tuple:
    """
    Classificeert een Groq/OpenAI-achtige foutmelding.

    Retourneert (kind, wait_seconds) waarbij kind één van:
      - "SIZE"    -> 413 Request too large (structureel; niet retryen)
      - "TPM"     -> 429 tokens-per-minuut limiet (kort wachten, retry)
      - "TPD"     -> 429 tokens-per-dag limiet (dag op; rest overslaan)
      - "OTHER"   -> overige 429 of onbekend (korte fallback-wait)
      - "UNKNOWN" -> geen Groq rate-limit fout
    wait_seconds kan None zijn als de wachttijd niet in de melding staat.
    """
    if "413" in msg or "Request too large" in msg:
        return ("SIZE", None)

    if "429" not in msg and "Rate limit" not in msg:
        return ("UNKNOWN", None)

    wait = None
    m = re.search(r"try again in (?:(\d+)m)?([\d.]+)s", msg)
    if m:
        minutes = int(m.group(1) or 0)
        seconds = float(m.group(2) or 0)
        wait = minutes * 60 + seconds

    if "tokens per day" in msg or "(TPD)" in msg:
        return ("TPD", wait)
    if "tokens per minute" in msg or "(TPM)" in msg:
        return ("TPM", wait)
    return ("OTHER", wait)


class DailyLimitReached(Exception):
    """Signaal dat de Groq daglimiet (TPD) is bereikt; verdere retries zinloos."""


class PromptTooLarge(Exception):
    """413: prompt past niet in de TPM-limiet; retry lost niets op."""


# ============================================================
# ANALYSE
# ============================================================

def analyze_ticker(ticker: str, trade_date: str, max_retries: int = 3) -> str:
    """
    Analyseer één ticker via TradingAgents.

    Retry-strategie:
      - 413 (SIZE)  : raise PromptTooLarge (geen retry)
      - 429 TPD     : raise DailyLimitReached (geen retry)
      - 429 TPM     : wacht `wait_seconds` (of 30s fallback) en retry
      - 429 OTHER   : korte fallback-wait en retry
      - anders      : raise
    """
    config = build_config()
    ta = TradingAgentsGraph(debug=False, config=config)

    for attempt in range(max_retries):
        try:
            _, decision = ta.propagate(ticker, trade_date)
            return decision
        except Exception as e:
            msg = str(e)
            kind, wait = parse_groq_error(msg)

            if kind == "SIZE":
                raise PromptTooLarge(msg)
            if kind == "TPD":
                raise DailyLimitReached(msg)

            if kind in ("TPM", "OTHER") and attempt < max_retries - 1:
                sleep_s = (wait if wait is not None else 30) + 3
                print(
                    f"  {ticker}: {kind}-limiet, {sleep_s:.0f}s wachten "
                    f"(poging {attempt + 1}/{max_retries})"
                )
                time.sleep(sleep_s)
                continue

            # Geen retries meer of onbekende fout: doorgeven
            raise

    raise RuntimeError(f"max_retries ({max_retries}) overschreden")


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
        help="Max aantal picks uit CSV om te analyseren (default: 10)",
    )
    parser.add_argument(
        "--max-tickers",
        type=int,
        default=0,
        help="Harde bovengrens op het aantal te analyseren tickers "
             "(0 = geen extra limiet bovenop --top-n / brondata). "
             "Aanbevolen: 5 om binnen de gratis Groq TPD te blijven.",
    )
    parser.add_argument(
        "--sleep",
        type=float,
        default=60.0,
        help="Seconden wachten tussen tickers om TPM-limiet te respecteren "
             "(default: 60)",
    )
    parser.add_argument(
        "--max-retries",
        type=int,
        default=3,
        help="Max pogingen per ticker bij 429 TPM (default: 3)",
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

    # Harde bovengrens om TPD-uitputting te beperken
    if args.max_tickers and len(tickers) > args.max_tickers:
        print(f"Beperkt tot {args.max_tickers} tickers (van {len(tickers)}).")
        tickers = tickers[: args.max_tickers]

    print(f"Bron: {bron_label}")
    print(f"Tickers ({len(tickers)}): {', '.join(tickers)}")
    print(f"Datum: {trade_date}")
    print(f"Model: {build_config()['deep_think_llm']}")
    print(f"Slaap tussen tickers: {args.sleep:.0f}s")
    print("")

    # Analyse per ticker
    blocks = []
    daily_limit_hit = False
    skipped_due_to_daily = []

    for idx, ticker in enumerate(tickers):
        if daily_limit_hit:
            blocks.append(f"⏭️ <b>{ticker}</b>: overgeslagen (Groq daglimiet TPD bereikt)")
            skipped_due_to_daily.append(ticker)
            continue

        print(f"Analyseren: {ticker} ...", flush=True)
        try:
            decision = analyze_ticker(ticker, trade_date, max_retries=args.max_retries)
            blocks.append(f"📊 <b>{ticker}</b>\n{decision}\n")
            print(f"  {ticker}: OK")
        except DailyLimitReached as e:
            daily_limit_hit = True
            skipped_due_to_daily.append(ticker)
            print(f"  {ticker}: DAGLIMIET TPD bereikt — verdere tickers worden overgeslagen.")
            blocks.append(
                f"⚠️ <b>{ticker}</b>: daglimiet Groq TPD bereikt — "
                f"resterende tickers overgeslagen"
            )
        except PromptTooLarge:
            print(f"  {ticker}: 413 (prompt te groot), overgeslagen.")
            blocks.append(
                f"⚠️ <b>{ticker}</b>: prompt te groot voor gratis Groq-tier (413) — "
                f"overgeslagen"
            )
        except Exception as e:
            msg = str(e)
            print(f"  {ticker}: fout — {msg[:200]}")
            blocks.append(f"⚠️ <b>{ticker}</b>: analyse mislukt ({msg[:200]})")

        # Pauze tussen tickers om TPM-limiet te respecteren
        if idx < len(tickers) - 1 and not daily_limit_hit:
            print(f"  ... {args.sleep:.0f}s wachten ...")
            time.sleep(args.sleep)

    # Samenvatting
    n_ok = sum(1 for b in blocks if b.startswith("📊"))
    n_fail = sum(1 for b in blocks if b.startswith("⚠️"))
    n_skip = sum(1 for b in blocks if b.startswith("⏭️"))
    header = (
        f"TradingAgents-analyse — {bron_label} ({trade_date}):\n"
        f"✅ {n_ok} geanalyseerd · ⚠️ {n_fail} mislukt · ⏭️ {n_skip} overgeslagen\n\n"
    )
    message = header + "\n".join(blocks)

    if daily_limit_hit:
        message += (
            "\n\n💡 <i>Daglimiet bereikt. Spreid de analyse over meerdere runs "
            "of verlaag --top-n / --max-tickers.</i>"
        )

    # Versturen
    if args.channel in ("telegram", "both"):
        send_telegram(message)
        print("Verstuurd naar Telegram.")
    if args.channel in ("email", "both"):
        send_email(f"TradingAgents {bron_label} {trade_date}", message)
        print("Verstuurd naar Gmail.")

    # Exit-code zodat je in GitHub Actions kunt detecteren dat de run
    # gedeeltelijk is mislukt (bijv. om een retry-job te triggeren).
    if n_ok == 0 and n_fail > 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
