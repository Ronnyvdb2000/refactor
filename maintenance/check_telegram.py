"""Handmatige Telegram-verbindingstest; importeren verstuurt geen berichten.

Start vanuit de repository-root: python -m maintenance.check_telegram.
"""
import argparse
import os

MESSAGES = {
    'bot': "✅ GitHub-verbinding werkt! Ronny's bot is online.",
    'verbinding': 'Hallo! De verbinding werkt! 🚀',
}


def check_telegram(message='bot'):
    """Send one explicit test message; return a process status without secrets."""
    if message not in MESSAGES:
        raise ValueError('Onbekende berichtkeuze')
    token = os.environ.get('TELEGRAM_TOKEN', '').strip()
    chat = os.environ.get('TELEGRAM_CHAT_ID', '').strip()
    if not token or not chat:
        print('FOUT: TELEGRAM_TOKEN en/of TELEGRAM_CHAT_ID ontbreekt.')
        return 1
    import requests
    try:
        response = requests.post(
            f'https://api.telegram.org/bot{token}/sendMessage',
            json={'chat_id': chat, 'text': MESSAGES[message]}, timeout=20,
        )
        response.raise_for_status()
        result = response.json()
        if not isinstance(result, dict) or result.get('ok') is not True:
            print('FOUT: Telegram heeft het testbericht niet bevestigd.')
            return 1
    except (requests.RequestException, ValueError):
        # Request exceptions and API descriptions can contain the token or chat ID.
        print('FOUT: Telegram-verzoek mislukt; controleer verbinding en instellingen.')
        return 1
    print('SUCCES: Testbericht verzonden naar Telegram.')
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--message', choices=MESSAGES, default='bot')
    args = parser.parse_args(argv)
    from dotenv import load_dotenv
    load_dotenv()
    return check_telegram(args.message)


if __name__ == '__main__':
    raise SystemExit(main())
