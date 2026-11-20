"""Manual tools must never send messages or download prices at import time."""
import importlib
from pathlib import Path
from unittest.mock import MagicMock, patch
import requests
import pytest
from maintenance import check_telegram


@pytest.fixture
def credentials(monkeypatch):
    monkeypatch.setenv('TELEGRAM_TOKEN', 'private-token')
    monkeypatch.setenv('TELEGRAM_CHAT_ID', 'private-chat')


def test_imports_do_not_contact_telegram_or_load_yahoo():
    import sys
    with patch.object(requests, 'post') as post, patch.dict(sys.modules, {'yfinance': None}):
        importlib.reload(check_telegram)
        importlib.import_module('maintenance.diag_koersen')
    post.assert_not_called()


@pytest.mark.parametrize('module', ['check_telegram', 'diag_koersen'])
def test_cli_help_does_not_need_live_dependencies(module):
    # No provider dependencies are imported by argument parsing.
    import subprocess
    import sys
    result = subprocess.run([sys.executable, '-m', 'maintenance.' + module, '--help'],
                            cwd=Path(__file__).resolve().parents[1], capture_output=True,
                            text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    assert 'usage:' in result.stdout


def test_missing_credentials_do_not_send(monkeypatch):
    monkeypatch.delenv('TELEGRAM_TOKEN', raising=False)
    with patch.object(requests, 'post') as post:
        assert check_telegram.check_telegram() == 1
    post.assert_not_called()


@pytest.mark.parametrize('message', ['bot', 'verbinding'])
def test_explicit_send_uses_timeout_and_confirms_success(credentials, message):
    response = MagicMock()
    response.json.return_value = {'ok': True}
    with patch.object(requests, 'post', return_value=response) as post:
        assert check_telegram.check_telegram(message) == 0
    assert post.call_args.kwargs['timeout'] == 20
    assert post.call_args.kwargs['json']['text'] == check_telegram.MESSAGES[message]
    response.raise_for_status.assert_called_once()


@pytest.mark.parametrize('failure', ['http', 'timeout', 'json', 'rejected', 'list'])
def test_delivery_failure_fails_without_printing_secrets(credentials, capsys, failure):
    response = MagicMock()
    response.json.return_value = {'ok': False, 'description': 'private-token private-chat'}
    if failure == 'http':
        response.raise_for_status.side_effect = requests.HTTPError('private-token')
    if failure == 'json':
        response.json.side_effect = ValueError('private-token')
    if failure == 'list':
        response.json.return_value = []
    with patch.object(requests, 'post', return_value=response,
                      side_effect=requests.Timeout('private-token') if failure == 'timeout' else None):
        assert check_telegram.check_telegram() == 1
    output = capsys.readouterr().out
    assert 'private-token' not in output and 'private-chat' not in output


def test_main_preserves_failure_status(credentials):
    with patch.object(check_telegram, 'check_telegram', return_value=1), patch('dotenv.load_dotenv'):
        assert check_telegram.main(['--message', 'verbinding']) == 1
