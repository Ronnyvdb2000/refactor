import hashlib
from datetime import date
from unittest.mock import MagicMock

import pytest
import requests
from analyses.paper_telegram import deliver, milestones, notify
from analyses.paper_dispatch import waiting_for_entry


def sample():
    protocol=dict(initial_cash=1000,models=[dict(model='a_par',version='a_par_model_v1')],
                  horizon=10,commission_bps=5,slippage_bps=10)
    books=['a_par:a_par_model_v1','equal_universe','benchmark']
    snapshot=dict(sessions=['2026-11-30','2026-12-11'],universe=['A','B'])
    result=dict(status='complete',entry='2026-11-30',exit='2026-12-11',
                ending=dict(zip(books,[1100,1050,1020])))
    batch=dict(id='batch',snapshot=snapshot,status='complete',result=result)
    nav=[dict(book=b,day=d,equity=v) for b in books for d,v in [('2026-11-30',900),('2026-12-11',result['ending'][b])]]
    return protocol,[batch],nav


def test_closed_message_has_net_period_cumulative_drawdown_and_matched_baselines():
    p,b,n=sample();events=milestones(p,b,n)
    assert events[0][0]=='started:batch' and events[1][0]=='closed:batch'
    message=events[1][2]
    assert 'Netto periode: +10.00%' in message
    assert 'Cumulatief: +10.00%' in message
    assert 'Maximale terugval tot deze datum: -10.00%' in message
    assert 'vs kandidaten: +5.00 procentpunt' in message
    assert 'vs SPY: +8.00 procentpunt' in message
    assert 'nog niet bewezen' in message
    assert len(message.encode('utf-16-le'))//2<3900


def test_second_period_uses_prior_ending_balance_not_new_capital():
    p,b,n=sample();second={**b[0], 'id':'second', 'result':{**b[0]['result'], 'entry':'2026-12-14','exit':'2026-12-25',
                             'ending':dict.fromkeys(b[0]['result']['ending'],1100)}}
    nav=n+[dict(book=k,day='2026-12-25',equity=1100) for k in second['result']['ending']]
    msg=milestones(p,b+[second],nav)[2][2]
    assert 'Afgeronde perioden: 2' in msg
    assert 'Netto periode: +0.00%' in msg
    assert 'Cumulatief: +10.00%' in msg


def test_pending_only_gets_start_confirmation_and_missing_nav_blocks_results():
    p,b,n=sample();b[0]['status']='pending'
    assert len(milestones(p,b,n))==1
    b[0]['status']='complete'
    with pytest.raises(ValueError,match='Dagwaarderingen'):
        milestones(p,b,[])


@pytest.mark.parametrize('code,payload,status',[
    (200,{'ok':True,'result':{'message_id':123}},'sent'),
    (429,{'ok':False},'failed'),(403,{'ok':False},'failed'),
    (500,{'ok':False},'uncertain'),(200,{'ok':True},'uncertain'),
    (200,[], 'uncertain')])
def test_single_http_attempt_and_safe_statuses(code,payload,status):
    response=MagicMock(status_code=code);response.json.return_value=payload
    post=MagicMock(return_value=response)
    result=deliver('text','secret','chat',post=post)
    assert result[0]==status
    post.assert_called_once()
    assert 'parse_mode' not in post.call_args.kwargs['json']


def test_timeout_is_uncertain_without_retry_or_token_in_output(capsys):
    post=MagicMock(side_effect=requests.Timeout('https://api.telegram.org/botSECRET/sendMessage'))
    assert deliver('text','SECRET','chat',post=post)==('uncertain',None)
    post.assert_called_once()
    assert 'SECRET' not in capsys.readouterr().out


def db(previous=None,inserted=True):
    p,b,n=sample();b[0]['status']='pending'
    conn=MagicMock();cur=conn.cursor.return_value.__enter__.return_value
    cur.fetchone.side_effect=[{'locked':True},{'protocol':p},{'status':'claimed'} if inserted else None]+([previous] if not inserted else [])
    cur.fetchall.side_effect=[b,n]
    return conn,cur


def test_claim_commits_before_sending_and_success_is_saved():
    conn,cur=db();seen=[]
    def sender(*args):
        assert conn.commit.call_count==1
        seen.append(args)
        return 'sent',321
    assert notify(conn,'token','chat',sender)==1
    assert len(seen)==1
    update=[args for args,_ in cur.execute.call_args_list if args[0].startswith('UPDATE')][-1]
    assert update[1][:2]==('sent',321)


def test_sent_skips_even_after_format_change_and_uncertain_never_retries():
    conn,_=db(previous={'status':'sent','message_hash':'old'},inserted=False)
    sender=MagicMock()
    assert notify(conn,'token','chat',sender)==0
    sender.assert_not_called()
    p,b,n=sample();b[0]['status']='pending'
    message=milestones(p,b,n)[0][2]
    conn,_=db(previous={'status':'uncertain','message_hash':hashlib.sha256(message.encode()).hexdigest()},inserted=False)
    with pytest.raises(RuntimeError,match='onzeker'):
        notify(conn,'token','chat',sender)
    sender.assert_not_called()


def test_explicit_failure_can_retry_and_missing_secrets_never_claim():
    p,b,n=sample();b[0]['status']='pending'
    message=milestones(p,b,n)[0][2]
    conn,_=db(previous={'status':'failed','message_hash':hashlib.sha256(message.encode()).hexdigest()},inserted=False)
    sender=MagicMock(return_value=('sent',321))
    assert notify(conn,'token','chat',sender)==1
    conn=MagicMock()
    with pytest.raises(ValueError,match='ontbreken'):
        notify(conn,'','',sender)
    conn.cursor.assert_not_called()


@pytest.mark.parametrize('row,today,waiting',[
    (None,date(2026,11,27),False),
    (('pending','2026-11-30'),date(2026,11,27),True),
    (('pending','2026-11-30'),date(2026,11,30),True),
    (('pending','2026-11-30'),date(2026,12,1),False),
    (('missing_prices','2026-11-30'),date(2026,11,27),False)])
def test_no_price_fetch_before_first_closed_entry_session(row,today,waiting):
    conn=MagicMock();conn.cursor.return_value.__enter__.return_value.fetchone.return_value=row
    assert waiting_for_entry(conn,'trial',today) is waiting
