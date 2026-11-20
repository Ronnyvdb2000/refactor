import io
import json
from unittest.mock import patch

import pytest
from research.telegram import send_text, format_result


def test_missing_credentials(monkeypatch):
    monkeypatch.delenv("TELEGRAM_TOKEN", raising=False)
    with pytest.raises(RuntimeError, match="ontbreekt"):
        send_text("test")


def test_plain_text_chunks(monkeypatch):
    monkeypatch.setenv("TELEGRAM_TOKEN", "secret")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "123")
    with patch("research.telegram.urlopen", side_effect=lambda *a, **k: io.BytesIO(b'{"ok":true}')) as post:
        send_text("<test>" * 1000)
    parts = [json.loads(call.args[0].data) for call in post.call_args_list]
    assert ''.join(p['text'] for p in parts) == '<test>' * 1000
    assert all(len(p['text']) <= 1800 and 'parse_mode' not in p for p in parts)


def test_delivery_failure_redacts_token(monkeypatch):
    monkeypatch.setenv("TELEGRAM_TOKEN", "secret")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "123")
    for response in (lambda *a, **k: io.BytesIO(b'{"ok":false}'),
                     lambda *a, **k: (_ for _ in ()).throw(OSError('secret'))):
        with patch("research.telegram.urlopen", side_effect=response):
            with pytest.raises(RuntimeError) as error:
                send_text("test")
            assert "secret" not in str(error.value)


def test_report_labels_empty_simulation():
    text = format_result({'run_id': 'abc', 'manifest': {'currency': 'USD', 'policy': {'capital': 50000}},
                          'simulation': {'gross_exposure': 0, 'remaining_cash': 50000,
                                         'estimated_stop_loss': 0, 'positions': []}})
    assert 'geen orders' in text and 'Geen signalen' in text and 'USD' in text
