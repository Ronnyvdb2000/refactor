"""Telegram + email notificaties. Één implementatie voor alle bots."""
from __future__ import annotations
import os
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import requests


def _tg_creds():
    return os.getenv("TELEGRAM_TOKEN", ""), os.getenv("TELEGRAM_CHAT_ID", "")


def _email_creds():
    return (os.getenv("EMAIL_USER", ""), os.getenv("EMAIL_PASS", ""),
            os.getenv("EMAIL_RECEIVER", ""))


def send_telegram(text: str, *, parse_mode: str | None = "Markdown",
                  silent_on_missing: bool = True) -> bool:
    """Verstuurt een Telegram-bericht. Retourneert True bij succes.
    Zonder creds: print naar stdout (of niets bij silent_on_missing)."""
    token, chat_id = _tg_creds()
    if not token or not chat_id:
        if not silent_on_missing:
            print(text)
        return False
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {"chat_id": chat_id, "text": text, "disable_web_page_preview": True}
    if parse_mode:
        payload["parse_mode"] = parse_mode
    try:
        r = requests.post(url, json=payload, timeout=15)
        if r.status_code != 200:
            # Fallback zonder opmaak bij parse-fout
            payload.pop("parse_mode", None)
            requests.post(url, json=payload, timeout=15)
            return False
        return True
    except Exception as e:
        print(f"[WARN] Telegram: {e}")
        return False


# Aliassen voor backwards-compat met de originele bots
send_telegram_message = send_telegram


def send_email(subject: str, body: str, *, html_body: bool = False,
               silent_on_missing: bool = True) -> bool:
    user, pwd, ontvanger = _email_creds()
    if not user or not pwd or not ontvanger:
        if not silent_on_missing:
            print(f"[email] {subject}\n{body}")
        return False
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = user
    msg["To"] = ontvanger
    if html_body:
        msg.attach(MIMEText(body, "html", "utf-8"))
    else:
        clean = body.replace("*", "").replace("`", "").replace("•", "-").replace("_", "")
        msg.attach(MIMEText(clean, "plain", "utf-8"))
    try:
        with smtplib.SMTP("smtp.gmail.com", 587, timeout=30) as s:
            s.starttls()
            s.login(user, pwd)
            s.sendmail(user, ontvanger, msg.as_string())
        return True
    except Exception as e:
        print(f"[WARN] Email: {e}")
        return False
