import json
import subprocess
from pathlib import Path
import pytest
from querydoctor.ci.prepare import CoverageError, prepare_git_inputs
from querydoctor.ci.migrations import export_migrations


def commit(root, message):
    subprocess.run(['git','-C',str(root),'add','.'],check=True,capture_output=True)
    subprocess.run(['git','-C',str(root),'-c','user.name=QueryDoctor Test','-c','user.email=test@localhost','commit','-m',message],check=True,capture_output=True)
    return subprocess.check_output(['git','-C',str(root),'rev-parse','HEAD']).decode().strip()


def repository(tmp_path):
    root=tmp_path/'repo';root.mkdir()
    subprocess.run(['git','init',str(root)],check=True,capture_output=True)
    (root/'schema').mkdir();(root/'queries').mkdir()
    (root/'schema/001.sql').write_text('CREATE TABLE t (id int); CREATE INDEX t_id ON t(id);')
    (root/'queries/read.sql').write_text('SELECT id FROM t ORDER BY id')
    (root/'workload.json').write_text(json.dumps([{'id':'read','sql_file':'queries/read.sql','params':[]}]))
    (root/'ci.json').write_text(json.dumps({'sql_directories':['schema'],'workload':'workload.json'}))
    return root,commit(root,'baseline')


def test_git_inputs_compare_changed_queries_and_actual_schema(tmp_path):
    root,base=repository(tmp_path)
    (root/'schema/002.sql').write_text('DROP INDEX t_id;')
    (root/'queries/read.sql').write_text('SELECT id FROM t ORDER BY id DESC')
    head=commit(root,'candidate')
    baseline,candidate,workload,provenance=prepare_git_inputs(root,base,head,'ci.json',tmp_path/'out')
    assert len(list(baseline.glob('*.sql'))) == 1
    assert len(list(candidate.glob('*.sql'))) == 2
    query=json.loads(workload.read_text())[0]
    assert query['sql'] == 'SELECT id FROM t ORDER BY id'
    assert query['candidate_sql'].endswith('DESC')
    assert provenance['base_commit'] == base and provenance['head_commit'] == head


def test_unexported_alembic_or_unlisted_sql_never_silently_passes(tmp_path):
    root,base=repository(tmp_path)
    (root/'migrations').mkdir()
    (root/'migrations/001.py').write_text('def upgrade(): pass')
    head=commit(root,'uncovered migration')
    with pytest.raises(CoverageError,match='lack configured SQL exports'):
        prepare_git_inputs(root,base,head,'ci.json',tmp_path/'out')


def test_changed_parameters_cannot_be_reported_as_a_schema_regression(tmp_path):
    root,base=repository(tmp_path)
    (root/'workload.json').write_text(json.dumps([{'id':'read','sql_file':'queries/read.sql','params':[42]}]))
    head=commit(root,'changed parameters')
    with pytest.raises(CoverageError,match='parameters differ'):
        prepare_git_inputs(root,base,head,'ci.json',tmp_path/'out')


def test_exports_sql_prisma_and_alembic_without_applying_them(tmp_path):
    paths=export_migrations(tmp_path,'querydoctor-fix-0-0','CREATE INDEX ON t (id)')
    assert len(paths) == 3 and all(p.is_file() for p in paths)
    assert 'CONCURRENTLY' in paths[0].read_text()
    assert paths[1].name == 'migration.sql'
    assert 'autocommit_block' in paths[2].read_text()
    compile(paths[2].read_text(),str(paths[2]),'exec')

def test_native_migration_exporter_runs_against_each_exact_git_snapshot(tmp_path):
    import sys
    root,base=repository(tmp_path)
    (root/'migrations').mkdir()
    (root/'migrations/version.py').write_text('VALUE=1')
    (root/'export.py').write_text("from pathlib import Path\nprint('CREATE TABLE exported (id int); -- ' + Path('migrations/version.py').read_text())")
    config=json.loads((root/'ci.json').read_text())
    config['sql_directories']=[]
    config['schema_export']={'command':[sys.executable,'export.py'],'covers':['migrations']}
    (root/'ci.json').write_text(json.dumps(config))
    base=commit(root,'configure exporter')
    (root/'migrations/version.py').write_text('VALUE=2')
    head=commit(root,'native migration changed')
    baseline,candidate,_,_=prepare_git_inputs(root,base,head,'ci.json',tmp_path/'out')
    assert 'VALUE=1' in (baseline/'0000_export.sql').read_text()
    assert 'VALUE=2' in (candidate/'0000_export.sql').read_text()


def test_a_missing_or_uncommitted_input_says_which_file_and_revision(tmp_path):
    root, base = repository(tmp_path)
    with pytest.raises(CoverageError, match=r"missing\.json.*not found at revision.*committed"):
        prepare_git_inputs(root, base, base, 'missing.json', tmp_path / 'out')
