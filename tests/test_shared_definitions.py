"""Cross-entrypoint contracts and cost-unit regression checks."""
import ast
from pathlib import Path

import pytest

from common.costs import execution_defaults, execution_rates, net_signal_pct
from common.model_definitions import ML_FEATURES, parameter_scoring_defaults

ROOT = Path(__file__).resolve().parents[1]


def test_live_and_frozen_parameter_configuration_agree():
    from meta_models.a_par_model import ModelConfig
    from analyses.paper_trial import PROTOCOL
    config = ModelConfig()
    defaults = parameter_scoring_defaults()
    assert {key: getattr(config, key) for key in defaults} == PROTOCOL['model_config']
    defaults['top_n'] = 999
    assert parameter_scoring_defaults()['top_n'] == 10
    assert PROTOCOL['model_config']['top_n'] == 10


def test_all_eleven_feature_models_and_live_scorer_keep_identical_order():
    # Read assignment expressions without importing notification/live entrypoints.
    from common import model_definitions as shared
    contracts = {
        'models/xgboostV3.py': 'FEATURE_COLUMNS',
        'models/xgboostV3_gecontroleerd.py': 'FEATURE_COLUMNS',
        'models/xLightgbmV2.py': 'FEATURE_COLUMNS',
        'models/xLightgbmV2_gecontroleerd.py': 'FEATURES',
        'models/catboostV1.py': 'FEATURES',
        'models/elastic_net.py': 'FEATURES',
        'models/10/extra_trees.py': 'FEATURES',
        'models/score_xgboost.py': 'VERWACHTE_FEATURES',
    }
    copies = []
    for path, name in contracts.items():
        tree = ast.parse((ROOT/path).read_text())
        expression = next(node.value for node in tree.body if isinstance(node, ast.Assign)
            and any(isinstance(t, ast.Name) and t.id == name for t in node.targets))
        values = eval(compile(ast.Expression(expression), path, 'eval'), vars(shared))
        assert values == list(ML_FEATURES), path
        copies.append(values)
    copies[0].reverse()
    assert all(values == list(ML_FEATURES) for values in copies[1:])


def test_basis_points_and_frozen_overrides_are_preserved():
    from analyses.paper_trial import PROTOCOL
    assert {key: PROTOCOL[key] for key in execution_defaults()} == execution_defaults()
    assert execution_rates(execution_defaults()) == (0.0005, 0.001)
    frozen = dict(commission_bps=7, slippage_bps=3)
    assert execution_rates(frozen) == (0.0007, 0.0003)
    assert frozen == dict(commission_bps=7, slippage_bps=3)
    assert net_signal_pct(1.0, 20) == pytest.approx(0.8)
    assert net_signal_pct(-1.0, 20) == pytest.approx(-1.2)


@pytest.mark.parametrize('bad', [-1, float('nan'), float('inf')])
def test_invalid_costs_cannot_create_an_execution_or_signal_benefit(bad):
    with pytest.raises(ValueError):
        execution_rates(dict(commission_bps=bad, slippage_bps=0))
    with pytest.raises(ValueError):
        net_signal_pct(1.0, bad)


def requirements(path, seen=()):
    assert path not in seen, 'Circular requirements include'
    result = {}
    for raw in (ROOT/path).read_text().splitlines():
        line = raw.split('#', 1)[0].strip()
        if line.startswith('-r '):
            incoming = requirements(line[3:], (*seen, path))
        elif '==' in line:
            name, version = line.split('==')
            incoming = {name: version}
        else:
            continue
        for name, version in incoming.items():
            assert name not in result or result[name] == version, (path, name)
            result[name] = version
    return result


def test_every_model_profile_inherits_one_numerical_contract():
    base = requirements('requirements-numerical.txt')
    for path in ('requirements-analysis.txt', 'requirements-catboost.txt',
                 'requirements-elastic-net.txt', 'requirements-extra-trees.txt',
                 'requirements-paper.txt', 'requirements-europe-ml.txt',
                 'requirements-xgboost.txt', 'requirements-xgboost-legacy.txt',
                 'requirements-lightgbm.txt'):
        profile = requirements(path)
        assert {key: profile[key] for key in base} == base


def test_v3_training_and_live_scoring_install_identical_profile():
    import yaml
    for name in ('run_02xgboostV3.yml', 'score_xgboost.yml'):
        workflow = yaml.load((ROOT/'.github/workflows'/name).read_text(), Loader=yaml.BaseLoader)
        installs = [step['run'] for job in workflow['jobs'].values() for step in job['steps']
                    if 'pip install' in step.get('run', '')]
        assert any('-r requirements-xgboost.txt' in command for command in installs)
        assert not any('xgboost==' in command for command in installs)


def test_fingerprint_tracks_nested_requirements_and_rejects_cycles(tmp_path):
    from common.reproducibility import source_fingerprint
    (tmp_path/'common').mkdir()
    (tmp_path/'common/reproducibility.py').write_text((ROOT/'common/reproducibility.py').read_text())
    (tmp_path/'requirements-profile.txt').write_text('-r requirements-base.txt\n')
    base = tmp_path/'requirements-base.txt'
    base.write_text('numpy==1.26.4\n')
    first = source_fingerprint(tmp_path, ['requirements-profile.txt'])
    base.write_text('numpy==2.0.0\n')
    assert source_fingerprint(tmp_path, ['requirements-profile.txt']) != first
    base.write_text('-r requirements-profile.txt\n')
    with pytest.raises(ValueError, match='Cyclische'):
        source_fingerprint(tmp_path, ['requirements-profile.txt'])
