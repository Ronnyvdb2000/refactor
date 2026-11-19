from unittest.mock import patch
from common import notify


def test_report_splits_utf16_without_losing_text():
    body = 'A' * 3899 + '📊' * 2100 + '\nlaatste deel'
    with patch.object(notify, 'send_telegram', return_value=True) as send:
        assert notify.send_telegram_report(body)
    parts = [call.args[0] for call in send.call_args_list]
    assert ''.join(parts) == body
    assert all(len(p.encode('utf-16-le')) // 2 <= 3900 for p in parts)
    assert all(call.kwargs == {'parse_mode': None} for call in send.call_args_list)


def test_report_attempts_later_parts_after_failure(capsys):
    with patch.object(notify, 'send_telegram', side_effect=[False, True]) as send:
        assert not notify.send_telegram_report('x' * 4000)
    assert send.call_count == 2
    assert 'rapportdeel 1/2 niet verzonden' in capsys.readouterr().out
