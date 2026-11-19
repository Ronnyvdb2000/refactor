"""Exercise workflow shell inputs and the bridge without credentials or LLM calls."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import types
import yaml
import pytest

ROOT=Path(__file__).resolve().parents[1]
CONFIG=yaml.safe_load((ROOT/'.github/workflows/tradingagentsbridge.yml').read_text())
JOB=CONFIG['jobs']['run-bridge']
STEPS={s.get('name'):s for s in JOB['steps']}


def environment(**overrides):
    return {**os.environ,'SOURCE':'par','HORIZON':'10d','TOP_N':'5','CHANNEL':'telegram',
            'DATE_ARG':'','GROQ_MODEL':'openai/gpt-oss-20b',**overrides}


@pytest.fixture
def bridge(monkeypatch):
    modules={name:types.ModuleType(name) for name in ['tradingagents','tradingagents.graph',
             'tradingagents.graph.trading_graph','tradingagents.default_config']}
    modules['tradingagents.graph.trading_graph'].TradingAgentsGraph=object
    modules['tradingagents.default_config'].DEFAULT_CONFIG={'data_vendors':{'news_data':'yfinance'}}
    for name,module in modules.items():monkeypatch.setitem(sys.modules,name,module)
    spec=importlib.util.spec_from_file_location('_tested_trading_bridge',ROOT/'tradingagents_bridge.py')
    module=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_push_schedule_and_manual_defaults_select_par_generation():
    # The producer and consumer use the SAME normalized source for every event.
    assert JOB['env']['SOURCE']=="${{ inputs.source || 'par' }}"
    assert STEPS['Run a_par_model (indien source=par)']['if']=="env.SOURCE == 'par'"
    assert STEPS['Haal VCP-signalen uit de laatste geslaagde bronrun']['if']=="env.SOURCE == 'vcp'"
    assert STEPS['Run TradingAgents bridge']['env']['TELEGRAM_TOKEN']=='${{ secrets.TELEGRAM_TOKEN }}'


@pytest.mark.parametrize('top_n',['0','11','bad','5; touch injected'])
def test_invalid_top_n_fails_before_running_any_model(tmp_path,top_n):
    result=subprocess.run(['bash','-e','-o','pipefail','-c',STEPS['Valideer invoer']['run']],
        env=environment(TOP_N=top_n),cwd=tmp_path,capture_output=True,text=True)
    assert result.returncode!=0
    assert not (tmp_path/'injected').exists()


def test_valid_inputs_and_safe_date_arguments_reach_both_commands(tmp_path):
    result=subprocess.run(['bash','-e','-c',STEPS['Valideer invoer']['run']],
        env=environment(DATE_ARG='2026-11-06'),cwd=tmp_path,capture_output=True,text=True)
    assert result.returncode==0,result.stderr
    bindir=tmp_path/'bin';bindir.mkdir()
    fake=bindir/'python'
    fake.write_text('#!'+sys.executable+'\nimport json,sys\nfrom pathlib import Path\nargs=sys.argv[1:]\nif args[0]=="-u": args=args[1:]\nif args[0]=="-m": args=args[1:]\nPath(args[0]+".args").write_text(json.dumps(args[1:]))\n')
    fake.chmod(0o755)
    env=environment(PATH=str(bindir)+os.pathsep+os.environ['PATH'],DATE_ARG='2026-11-06')
    for name in ['Run a_par_model (indien source=par)','Run TradingAgents bridge']:
        result=subprocess.run(['bash','-e','-o','pipefail','-c',STEPS[name]['run']],
            cwd=tmp_path,env=env,capture_output=True,text=True)
        assert result.returncode==0,result.stderr
    producer=json.loads((tmp_path/'meta_models.a_par_model.args').read_text())
    consumer=json.loads((tmp_path/'tradingagents_bridge.py.args').read_text())
    assert producer[producer.index('--datum')+1]=='2026-11-06'
    assert consumer[consumer.index('--date')+1]=='2026-11-06'
    assert consumer[consumer.index('--source')+1]=='par'


def test_missing_par_picks_is_an_explicit_workflow_failure(tmp_path):
    result=subprocess.run(['bash','-e','-c',STEPS['Controleer par-picks']['run']],
        cwd=tmp_path,env=environment(),capture_output=True,text=True)
    assert result.returncode!=0 and '::error::' in result.stdout


def test_bridge_and_workflow_use_same_supported_model_and_context_limits(bridge,monkeypatch):
    monkeypatch.delenv('GROQ_MODEL',raising=False)
    monkeypatch.delenv('GROQ_MAX_TOKENS',raising=False)
    config=bridge.build_config()
    assert config['deep_think_llm']==config['quick_think_llm']=='openai/gpt-oss-20b'
    assert config['deep_think_llm'] in JOB['env']['GROQ_MODEL']
    assert config['max_tool_rounds']==1 and config['max_tokens']==1024
    assert config['news_article_limit']==3 and 'online_tools' not in config
    config['data_vendors']['news_data']='modified'
    assert bridge.DEFAULT_CONFIG['data_vendors']['news_data']=='yfinance'


def test_vcp_respects_top_n_and_preserves_telegram(bridge,monkeypatch):
    monkeypatch.setattr(sys,'argv',['bridge','--source','vcp','--top-n','2','--sleep','0'])
    monkeypatch.setattr(bridge,'lees_vcp_signals',lambda *_:['A','B','C'])
    calls,sent=[],[]
    monkeypatch.setattr(bridge,'analyze_ticker',lambda t,*a,**kw:calls.append(t) or 'HOLD')
    monkeypatch.setattr(bridge,'send_telegram',sent.append)
    bridge.main()
    assert calls==['A','B']
    assert sent and '2 geanalyseerd' in sent[0]


def test_daily_limit_stops_remaining_tickers_without_hiding_failure(bridge,monkeypatch):
    monkeypatch.setattr(sys,'argv',['bridge','--source','par','--sleep','0'])
    monkeypatch.setattr(bridge,'lees_par_picks',lambda *_:['A','B'])
    calls,sent=[],[]
    def limit(t,*a,**kw):
        calls.append(t)
        raise bridge.DailyLimitReached('TPD')
    monkeypatch.setattr(bridge,'analyze_ticker',limit)
    monkeypatch.setattr(bridge,'send_telegram',sent.append)
    with pytest.raises(SystemExit) as exc:bridge.main()
    assert exc.value.code==1 and calls==['A']
    assert 'daglimiet' in sent[0].lower()


def test_every_workflow_shell_block_parses():
    for step in JOB['steps']:
        if 'run' in step:
            result=subprocess.run(['bash','-n'],input=step['run'],text=True,capture_output=True)
            assert result.returncode==0,(step.get('name'),result.stderr)


def test_par_command_failure_is_not_hidden_by_tee(tmp_path):
    bindir=tmp_path/'bin';bindir.mkdir()
    fake=bindir/'python';fake.write_text('#!/bin/bash\nexit 7\n');fake.chmod(0o755)
    result=subprocess.run(['bash','-e','-c',STEPS['Run a_par_model (indien source=par)']['run']],
        cwd=tmp_path,env=environment(PATH=str(bindir)+os.pathsep+os.environ['PATH']),capture_output=True,text=True)
    assert result.returncode==7


def test_tradingagents_consumes_json_picks(tmp_path, monkeypatch, bridge):
    monkeypatch.chdir(tmp_path)
    Path('results').mkdir()
    Path('results/a_par_model_10d_picks.json').write_text('[{"ticker":"A"},{"ticker":"B"}]')
    assert bridge.lees_par_picks('10d', 1) == ['A']
