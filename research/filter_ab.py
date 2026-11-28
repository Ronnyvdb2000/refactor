"""Prospective A/B filter inputs. B changes only North America's VOL_MIN to zero.

No production universe writes, trades, retrospective fundamentals or fake returns.
The existing strategies and parameter portfolios are not replaced by this census.
"""
from common.costs import execution_defaults, execution_rates, FX_BPS_PER_SIDE
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import time

import numpy as np
from psycopg2.extras import Json, RealDictCursor

from scripts import bot_041mV2 as native

STUDY_VERSION=2
VENUES=('048','056','057')
INFO_FIELDS=('returnOnEquity','revenueGrowth','profitMargins','debtToEquity','totalDebt','sector',
             'currency','marketCap','freeCashflow','trailingPE','priceToBook','dividendYield',
             'currentRatio','earningsGrowth','enterpriseToEbitda','numberOfAnalystOpinions','quoteType')


def clean(value):
    if isinstance(value,dict):
        return {str(k):clean(v) for k,v in value.items()}
    if isinstance(value,(list,tuple)):
        return [clean(v) for v in value]
    if isinstance(value,(float,np.floating)):
        return float(value) if math.isfinite(value) else None
    if isinstance(value,np.integer):
        return int(value)
    return value


def fingerprint(value):
    return hashlib.sha256(json.dumps(clean(value),sort_keys=True,allow_nan=False).encode()).hexdigest()


def protocol():
    a=deepcopy(native.get_criteria('048'));b={**a,'VOL_MIN':0.0}
    return dict(version=STUDY_VERSION,A=a,B=b,horizon=10,top_n=10,initial_cash=100000,
                **execution_defaults(),hold_weeks=native.MAX_WEKEN_BUITEN,
                native_hash=hashlib.sha256(Path(native.__file__).read_bytes()).hexdigest(),
                capture_hash=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                historical_reconstruction_allowed=False,profitability_proven=False)


def paired_observation(ticker,info,hist,*,applied=True,blocked=False,now=None):
    now=now or datetime.now(timezone.utc)
    info=info or {};p=protocol()
    fund_ok,metrics,reason=native.check_fundamenteel(ticker,p['A'],info=info)
    if reason.startswith('api_fout'):
        reason='api_fout'
    data={ticker:hist}
    a=native.check_vol_liq(ticker,data,p['A'])
    b=native.check_vol_liq(ticker,data,p['B'])
    vol=turnover=quote_day=last_close=None;features={}
    if hist is not None and not isinstance(hist,str) and not hist.empty:
        close=hist['Close'].ffill()
        vol=float(close.pct_change().dropna().std()*np.sqrt(252))
        turnover=float((close*hist['Volume']).mean()) if 'Volume' in hist else 0.
        quote_day=str(hist.index[-1].date());last_close=float(close.iloc[-1])
        from scripts.bouw_generieke_technicals import bereken_indicatoren
        computed=bereken_indicatoren(hist)
        if not computed.empty:
            features=clean(computed.iloc[-1].to_dict())
    available=(bool(info) and reason!='geen_fundamentele_data' and 'returnOnEquity' in info and vol is not None and math.isfinite(vol)
               and turnover is not None and math.isfinite(turnover) and quote_day<now.date().isoformat()
               and (now.date()-datetime.fromisoformat(quote_day).date()).days<=7)
    return clean(dict(ticker=ticker,observed_at=now.isoformat(),quote_day=quote_day,last_close=last_close,
                     info={k:info.get(k) for k in INFO_FIELDS},features=features,
                     annual_volatility=vol,average_turnover=turnover,
                     fund_ok=fund_ok,fund_reason=reason,fund_metrics=metrics,
                     native_pass_A=bool(fund_ok and a[0]),native_pass_B=bool(fund_ok and b[0]),
                     pass_A=bool(fund_ok and a[0]) if available else None,
                     pass_B=bool(fund_ok and b[0]) if available else None,
                     vol_reason_A=a[3],vol_reason_B=b[3],trend=a[4],
                     applied=bool(applied),blocked=bool(blocked),available=available))


def compare_states(seed_a,seed_b,observations,*,blocked=(),actual_a=None,sample=False):
    """Same three-week grace rule; unknown data never invents a passed observation."""
    a=deepcopy(seed_a);b=deepcopy(seed_b)
    blocked=set(blocked)|{r['ticker'] for r in observations if r['blocked']}
    for obs in sorted(observations,key=lambda r:r['ticker']):
        if obs['applied']:
            native.update_master(a,obs['ticker'],obs['native_pass_A'],{})
            native.update_master(b,obs['ticker'],obs['native_pass_B'],{})
    def members(state):
        return {t for t,e in state.items() if e.get('status') in ('actief','zwakker') and t not in blocked}
    ma=members(a);mb=members(b)
    observed={r['ticker'] for r in observations}
    if sample:
        ma &= observed;mb &= observed
    actual=set(actual_a) if actual_a is not None else ma
    mismatch=sorted(actual^ma) if not sample else []
    return clean(dict(A_members=sorted(ma),B_members=sorted(mb),B_added=sorted(mb-ma),
                      A_count=len(ma),B_count=len(mb),B_added_count=len(mb-ma),
                      unavailable=sum(not r['available'] for r in observations),
                      passed_A=sum(r['pass_A'] is True for r in observations),
                      passed_B=sum(r['pass_B'] is True for r in observations),
                      quiet_extra_candidates=sorted(r['ticker'] for r in observations if r['pass_B'] is True and r['pass_A'] is False),
                      captured=len(observations),A_replay_mismatch=mismatch,
                      B_state=b,portfolio_ready=False,return_pct=None,
                      stage='waiting_for_counterfactual_strategy_inputs_and_future_outcomes',
                      profitability_proven=False))


class Recorder:
    def __init__(self,conn,venue,expected,seed_a,*,mode='sample',run_id=None):
        if venue not in VENUES:
            raise ValueError('Alleen de vooraf vastgelegde Amerikaanse lijsten')
        self.conn=conn;self.venue=venue;self.mode=mode;self.errors=0
        self.key=f"filter_ab:v{STUDY_VERSION}:{run_id or os.getenv('FILTER_AB_RUN_ID') or os.environ['GITHUB_RUN_ID']}:{venue}"
        self.expected=sorted(set(expected));self.seed_a=deepcopy(seed_a);self.seed_b=deepcopy(seed_a)
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT cycle_key,status,protocol,result FROM public.research_filter_ab_cycles WHERE venue=%s AND mode='weekly' AND protocol->>'version'=%s "
                        "ORDER BY created_at DESC LIMIT 1",(venue,str(STUDY_VERSION)))
            previous=cur.fetchone()
            if previous and mode=='weekly':
                if previous['cycle_key']!=self.key and previous['status']!='complete':
                    raise ValueError('Vorige wekelijkse A/B-ronde onvolledig; eerst de ontbrekende dekking herstellen')
                if any(previous['protocol'][k]!=protocol()[k] for k in ('A','B','hold_weeks')):
                    raise ValueError('Filtercriteria veranderd; een nieuwe studieversie is vereist')
                if previous['status']=='complete':
                    self.seed_b=previous['result']['B_state']
            source=dict(expected=self.expected,initial_A=self.seed_a,initial_B=self.seed_b,
                        source_sha=os.getenv('GITHUB_SHA'))
            cur.execute('INSERT INTO public.research_filter_ab_cycles(cycle_key,venue,mode,protocol,source) '
                        'VALUES(%s,%s,%s,%s,%s) ON CONFLICT(cycle_key) DO NOTHING',
                        (self.key,venue,mode,Json(protocol()),Json(source)))
            cur.execute('SELECT protocol,source,status FROM public.research_filter_ab_cycles WHERE cycle_key=%s',(self.key,))
            saved=cur.fetchone()
            if saved['protocol']!=protocol():
                raise ValueError('Protocol gewijzigd; start een nieuwe observatieronde')
            if saved['source']['expected']!=self.expected:
                raise ValueError('Bronlijst gewijzigd tijdens deze ronde')
            self.seed_a=saved['source']['initial_A'];self.seed_b=saved['source']['initial_B']
        conn.commit()

    @classmethod
    def open(cls,*args,**kwargs):
        import psycopg2
        conn=psycopg2.connect(os.environ['SUPABASE_DB_URL'],connect_timeout=20)
        try:
            return cls(conn,*args,**kwargs)
        except Exception:
            conn.close()
            raise

    def observe_safe(self,ticker,info,hist,**kwargs):
        try:
            if ticker not in self.expected:
                raise ValueError('Ticker staat niet in de bevroren bronlijst')
            observation=paired_observation(ticker,info,hist,**kwargs)
            with self.conn.cursor() as cur:
                cur.execute('INSERT INTO public.research_filter_ab_observations(cycle_key,ticker,observation,observation_hash) '
                            'VALUES(%s,%s,%s,%s) ON CONFLICT(cycle_key,ticker) DO NOTHING',
                            (self.key,ticker,Json(observation),fingerprint(observation)))
            self.conn.commit()
        except Exception:
            self.conn.rollback();self.errors+=1
            print('[A/B] Observatie niet opgeslagen; dekking wordt als onvolledig gemeld.',flush=True)

    def finish_safe(self,actual_a=None,blocked=()):
        try:
            with self.conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute('SELECT observation FROM public.research_filter_ab_observations WHERE cycle_key=%s ORDER BY ticker',(self.key,))
                observations=[r['observation'] for r in cur.fetchall()]
            result=compare_states(self.seed_a,self.seed_b,observations,blocked=blocked,
                                  actual_a=actual_a,sample=self.mode=='sample')
            result.update(expected=len(self.expected),archive_errors=self.errors,mode=self.mode,venue=self.venue)
            complete=({r['ticker'] for r in observations}==set(self.expected) and not self.errors
                      and not result['A_replay_mismatch'])
            status='complete' if complete else 'partial'
            with self.conn.cursor() as cur:
                cur.execute("UPDATE public.research_filter_ab_cycles SET status=%s,result=%s WHERE cycle_key=%s AND status<>'complete'",
                            (status,Json(result),self.key))
            self.conn.commit()
            print(json.dumps(dict(cycle_key=self.key,status=status,**{k:v for k,v in result.items() if k!='B_state'})),flush=True)
            return status,result
        except Exception:
            self.conn.rollback();print('[A/B] Afronding niet bevestigd; geen rendementsclaim.',flush=True)
            return 'partial',{}
        finally:
            self.conn.close()


def collect_sample(limit=60,budget_minutes=15):
    from common.io import load_tickers_from_file
    deadline=time.monotonic()+budget_minutes*60
    all_complete=True
    for venue in VENUES:
        all_tickers=load_tickers_from_file(native.pad_bron(venue))
        tickers=sorted(all_tickers,key=lambda t:hashlib.sha256(t.encode()).hexdigest())[:max(1,limit//len(VENUES))]
        recorder=Recorder.open(venue,tickers,native.laad_master(venue))
        histories=native.batch_download(tickers)
        for ticker in tickers:
            if time.monotonic()>=deadline:
                break
            capture={}
            _,_,reason=native.check_fundamenteel(ticker,native.get_criteria(venue),capture=capture)
            hist=histories.get(ticker)
            recorder.observe_safe(ticker,capture.get('info',{}),hist,
                                  applied='geen_fundamentele_data' not in reason and hist is not None and not isinstance(hist,str),
                                  blocked=hist is None)
            time.sleep(native.SLEEP_INFO)
        status,_=recorder.finish_safe()
        all_complete=all_complete and status=='complete'
    return all_complete


def notify():
    import psycopg2
    from analyses.paper_telegram import deliver
    token=os.getenv('TELEGRAM_TOKEN');chat=os.getenv('TELEGRAM_CHAT_ID')
    if not token or not chat:
        raise ValueError('Telegraminstellingen ontbreken')
    run_id=os.getenv('FILTER_AB_RUN_ID') or os.environ['GITHUB_RUN_ID'];key=f'filter_ab:v{STUDY_VERSION}:'+run_id
    with psycopg2.connect(os.environ['SUPABASE_DB_URL'],connect_timeout=20) as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute('SELECT venue,mode,status,result FROM public.research_filter_ab_cycles WHERE cycle_key LIKE %s ORDER BY venue',
                        (key+':%',))
            rows=cur.fetchall()
        if not rows:
            raise ValueError('Geen opgeslagen A/B-observaties voor deze run')
        text=['🧪 Filterproef A/B — dataverzameling',
              'A: huidige filter (minimaal 20% volatiliteit). B: dezelfde regels zonder minimumvolatiliteit.',
              'Wekelijkse uitstelregels blijven voor beide varianten gelijk.']
        missing=sorted(set(VENUES)-{r['venue'] for r in rows})
        if missing:
            text.append('Ontbrekende lijsten in deze ronde: '+', '.join(missing))
        for row in rows:
            r=row['result']
            text.append(f"{row['venue']} ({row['mode']}, {row['status']}): {r.get('captured',0)}/{r.get('expected','?')} vastgelegd; "
                        f"criteria A {r.get('passed_A','?')}, B {r.get('passed_B','?')}; "
                        f"leden A {r.get('A_count','?')}, B {r.get('B_count','?')}, extra B {r.get('B_added_count','?')}; "
                        f"onvoldoende data {r.get('unavailable','?')}.")
        text.extend(['Een steekproef vertegenwoordigt niet het volledige beursuniversum.',
                     'Nog geen rendementsuitslag. Historische afwijzingen zijn niet volledig opgeslagen.',
                     'Voor de portefeuillevergelijking zijn identieke strategiegegevens voor de nieuwe B-aandelen en latere koersen nodig.',
                     'De bestaande selecties en paperproef blijven apart doorlopen.'])
        text='\n'.join(text)
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute('INSERT INTO public.research_filter_ab_notices(run_key,message) VALUES(%s,%s) '
                        'ON CONFLICT(run_key) DO NOTHING RETURNING status',(key,text))
            inserted=cur.fetchone()
            if not inserted:
                cur.execute('SELECT status FROM public.research_filter_ab_notices WHERE run_key=%s FOR UPDATE',(key,))
                if cur.fetchone()['status']=='sent':
                    return 0
                raise RuntimeError('Vorige bezorging niet bevestigd; geen automatische herhaling')
        conn.commit()
        status,message_id=deliver(text,token,chat)
        with conn.cursor() as cur:
            cur.execute('UPDATE public.research_filter_ab_notices SET status=%s,message_id=%s WHERE run_key=%s',
                        (status,message_id,key))
        conn.commit()
        if status!='sent':
            raise RuntimeError('Telegrambezorging niet bevestigd')
    print('A/B-filterproef: 1 Telegrambericht bevestigd.')
    return 1


def main():
    import argparse
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=['sample','notify'])
    parser.add_argument('--limit',type=int,default=60)
    args=parser.parse_args()
    if args.action=='notify':
        notify()
    else:
        if not 3<=args.limit<=300:
            parser.error('Steekproeflimiet moet tussen 3 en 300 liggen')
        if not collect_sample(args.limit):
            raise SystemExit('A/B-steekproef onvolledig; opgeslagen dekking wordt apart gemeld')


if __name__=='__main__':
    main()

