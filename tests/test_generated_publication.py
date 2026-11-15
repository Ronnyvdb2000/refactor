"""Exercise publication against isolated bare Git repositories, never GitHub."""
import subprocess
from pathlib import Path

import pytest

from maintainance.push_generated_commit import publish


def git(cwd, *args):
    return subprocess.check_output(['git', *args], cwd=cwd, text=True, stderr=subprocess.STDOUT).strip()


def commit(cwd, path, value):
    (cwd/path).write_text(value)
    git(cwd,'add',path)
    git(cwd,'commit','-m',path)


@pytest.fixture
def copies(tmp_path, monkeypatch):
    monkeypatch.delenv('GITHUB_REPOSITORY', raising=False)
    remote=tmp_path/'remote.git'
    git(tmp_path,'init','--bare','--initial-branch=main',str(remote))
    clones=[]
    for name in ['first','second']:
        clone=tmp_path/name
        git(tmp_path,'clone',str(remote),str(clone))
        git(clone,'config','user.email','offline@example.invalid')
        git(clone,'config','user.name','Offline test')
        clones.append(clone)
    first,second=clones
    commit(first,'source.py','initial\n')
    commit(first,'generated.csv','old\n')
    git(first,'push','origin','HEAD:main')
    git(second,'pull','--ff-only','origin','main')
    return remote,first,second


def test_publication_preserves_concurrent_unrelated_code_change(copies,monkeypatch):
    remote,first,second=copies
    commit(first,'generated.csv','new report\n')
    commit(second,'source.py','concurrent correction\n')
    git(second,'push','origin','HEAD:main')
    monkeypatch.chdir(first)
    publish()
    assert git(remote,'show','main:source.py')=='concurrent correction'
    assert git(remote,'show','main:generated.csv')=='new report'


def test_conflicting_generated_outputs_are_not_automatically_overwritten(copies,monkeypatch):
    remote,first,second=copies
    commit(first,'generated.csv','first report\n')
    commit(second,'generated.csv','second report\n')
    git(second,'push','origin','HEAD:main')
    monkeypatch.chdir(first)
    with pytest.raises(RuntimeError,match='Rebaseconflict'):
        publish()
    assert git(remote,'show','main:generated.csv')=='second report'
    assert not (first/'.git/rebase-merge').exists()


def test_uncommitted_code_stops_publication(copies,monkeypatch):
    remote,first,_=copies
    before=git(remote,'rev-parse','main')
    (first/'source.py').write_text('uncommitted change\n')
    monkeypatch.chdir(first)
    with pytest.raises(RuntimeError,match='uncommitted'):
        publish()
    assert git(remote,'rev-parse','main')==before


def test_other_repository_cannot_use_refactor_publication_helper(copies,monkeypatch):
    _,first,_=copies
    monkeypatch.chdir(first)
    monkeypatch.setenv('GITHUB_REPOSITORY','Ronnyvdb2000/refactor-lab')
    with pytest.raises(RuntimeError,match='restricted'):
        publish()
