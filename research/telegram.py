"""Plain-text Telegram research reports using the existing repository secrets."""
import json
import os
from urllib.request import Request, urlopen


def send_text(text):
    token = os.environ.get("TELEGRAM_TOKEN")
    chat = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat:
        raise RuntimeError("TELEGRAM_TOKEN of TELEGRAM_CHAT_ID ontbreekt")
    # Small chunks also remain below Telegram's limit for non-BMP Unicode.
    for start in range(0, len(text), 1800):
        body = json.dumps({"chat_id": chat, "text": text[start:start + 1800]}).encode()
        request = Request("https://api.telegram.org/bot" + token + "/sendMessage",
                          data=body, headers={"Content-Type": "application/json"}, method="POST")
        try:
            with urlopen(request, timeout=20) as response:
                result = json.load(response)
            if result.get("ok") is not True:
                raise ValueError("Telegram API rejected message")
        except Exception:
            # Never include request URLs or exception text: they can contain the token.
            raise RuntimeError("Telegram-rapportage mislukt; mogelijk gedeeltelijk verzonden") from None


def format_result(result):
    manifest, simulation = result["manifest"], result["simulation"]
    currency = manifest["currency"]
    lines = ["Onderzoek — allocatiesimulatie (geen orders)",
             f"Run: {result['run_id']}",
             f"Valuta: {currency} | fictief kapitaal: {manifest['policy']['capital']:,.2f}",
             f"Blootstelling: {simulation['gross_exposure']:,.2f}",
             f"Resterende cash: {simulation['remaining_cash']:,.2f}",
             f"Geschat totaal stopverlies incl. kosten: {simulation['estimated_stop_loss']:,.2f}",
             "Stops bieden geen garantie bij koersgaten."]
    if not simulation["positions"]:
        lines.append("Geen signalen in het invoermandje.")
    for p in simulation["positions"]:
        lines.append(f"{p['ticker']}: {p['shares']} stuks | instap {p['entry']:.4g} | "
                     f"stop {p['stop']:.4g} | kosten instap {p['entry_commission']:.2f} | "
                     f"geschat stopverlies {p['estimated_stop_loss']:.2f} | {p['reason']}")
    return "\n".join(lines)


if __name__ == "__main__":
    status = os.environ.get("RESEARCH_TEST_STATUS", "onbekend")
    url = os.environ.get("RESEARCH_RUN_URL", "")
    send_text(f"Research principles checks: {status}\nAlleen offline tests; geen aandelenselectie of simulatie uitgevoerd.\n{url}")
