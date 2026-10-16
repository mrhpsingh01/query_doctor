"""Prepare complete baseline/candidate SQL snapshots and workloads from Git revisions.

The config explicitly declares coverage. Changes outside that coverage fail closed instead
of producing a green demo report for migrations that were never exercised.
"""
from __future__ import annotations

import json
import io
import tarfile
import tempfile
import subprocess
from pathlib import Path, PurePosixPath


class CoverageError(ValueError):
    pass


def git(root: Path, *args: str) -> bytes:
    return subprocess.run(['git', '-C', str(root), *args], check=True, capture_output=True).stdout


def read(root: Path, revision: str, path: str) -> str:
    try:
        return git(root, 'show', f'{revision}:{path}').decode('utf-8-sig')
    except subprocess.CalledProcessError as exc:
        raise CoverageError(f'{path} was not found at revision {revision[:12]}: is it committed on that branch?') from exc


def safe_path(path: str) -> str:
    if not isinstance(path, str) or '\\' in path:
        raise CoverageError('Config paths must be relative POSIX paths')
    parsed = PurePosixPath(path)
    if parsed.is_absolute() or '..' in parsed.parts or not parsed.parts or ':' in path:
        raise CoverageError('Config paths must remain inside the repository')
    return str(parsed)


def sql_paths(root, revision, directories):
    entries = git(root, 'ls-tree', '-rz', '--full-tree', revision).split(b'\0')
    selected = []
    for entry in entries:
        if not entry:
            continue
        metadata, name = entry.split(b'\t', 1)
        path = name.decode('utf-8')
        if any(path.startswith(directory + '/') for directory in directories) and path.endswith('.sql'):
            if metadata.split()[0] != b'100644' and metadata.split()[0] != b'100755':
                raise CoverageError('SQL inputs must be regular files')
            selected.append(path)
    # Config directory order controls schema -> migration -> seed order; filenames sort within each directory.
    return list(dict.fromkeys(p for directory in directories for p in sorted(selected) if p.startswith(directory + '/')))


def export_schema(root: Path, revision: str, exporter: dict) -> str:
    command = exporter.get('command')
    if not isinstance(command, list) or not command or any(not isinstance(arg, str) for arg in command):
        raise CoverageError('schema_export.command must be an argument list, never a shell string')
    with tempfile.TemporaryDirectory(prefix='querydoctor-export-') as temporary:
        checkout = Path(temporary)
        # Materialize this revision without changing the caller's checkout or registering a worktree.
        with tarfile.open(fileobj=io.BytesIO(git(root, 'archive', revision))) as archive:
            for member in archive.getmembers():
                if member.issym() or member.islnk():
                    raise CoverageError('Schema export snapshots may not contain symlinks')
            archive.extractall(checkout, filter='data')
        relative = exporter.get('working_directory', '.')
        working = checkout if relative == '.' else checkout / safe_path(relative)
        try:
            result = subprocess.run(command, cwd=working, check=True, capture_output=True, timeout=120)
        except (subprocess.SubprocessError, OSError) as exc:
            raise CoverageError('Schema export failed; install the migration tool dependencies in CI and verify the configured offline command') from exc
        sql = result.stdout.decode('utf-8-sig')
        if not sql.strip():
            raise CoverageError('Schema exporter returned no SQL')
        return sql


def prepare_git_inputs(root: Path, base_ref: str, head_ref: str, config_path: str, output: Path):
    # Resolve to commit IDs before embedding revision:path. No shell strings are evaluated.
    base = git(root, 'rev-parse', '--verify', '--end-of-options', base_ref + '^{commit}').decode().strip()
    head = git(root, 'rev-parse', '--verify', '--end-of-options', head_ref + '^{commit}').decode().strip()
    config = json.loads(read(root, head, safe_path(config_path)))
    directories = [safe_path(d) for d in config.get('sql_directories', [])]
    exporter = config.get('schema_export')
    export_coverage = [safe_path(p) for p in exporter.get('covers', [])] if exporter else []
    workload_path = safe_path(config['workload'])
    current = json.loads(read(root, head, workload_path))
    original = json.loads(read(root, base, workload_path))
    if not isinstance(current, list) or not isinstance(original, list):
        raise CoverageError('Workloads must be JSON lists')
    source_paths = {workload_path}
    old_by_id = {q['id']: q for q in original}
    merged = []
    if len({q['id'] for q in current}) != len(current):
        raise CoverageError('Workload query IDs must be unique')
    for query in current:
        old = old_by_id.get(query['id'], query)
        query = dict(query)
        old_sql = read(root, base, safe_path(old['sql_file'])) if old.get('sql_file') and query['id'] in old_by_id else old.get('sql')
        new_sql = read(root, head, safe_path(query['sql_file'])) if query.get('sql_file') else query.get('sql')
        if old.get('params', []) != query.get('params', []):
            raise CoverageError('Baseline/candidate parameters differ; provide a stable seeded comparison workload')
        if query.get('sql_file'):
            source_paths.add(safe_path(query['sql_file']))
        if old.get('sql_file'):
            source_paths.add(safe_path(old['sql_file']))
        merged.append({**query, 'sql': old_sql or new_sql, 'candidate_sql': new_sql,
                       'file': query.get('sql_file', query.get('file', workload_path))})
    for old in original:
        if old.get('sql_file'):
            source_paths.add(safe_path(old['sql_file']))
    changed = [p.decode() for p in git(root, 'diff', '--name-only', '-z', base, head).split(b'\0') if p]
    uncovered = []
    for path in changed:
        related = path.endswith(('.sql', '.prisma')) or any(part in ('migrations', 'alembic', 'queries') for part in PurePosixPath(path).parts)
        covered_sql = path.endswith('.sql') and any(path.startswith(d + '/') for d in directories)
        if related and not covered_sql and path not in source_paths and not any(path == p or path.startswith(p + '/') for p in export_coverage):
            uncovered.append(path)
    if uncovered:
        raise CoverageError('Changed database files lack configured SQL exports/workload coverage: ' + ', '.join(uncovered))
    output.mkdir(parents=True, exist_ok=True)
    for side, revision in [('baseline', base), ('candidate', head)]:
        target = output / side
        target.mkdir()
        paths = sql_paths(root, revision, directories)
        if exporter:
            (target / '0000_export.sql').write_text(export_schema(root, revision, exporter), encoding='utf-8')
        if not paths and not exporter:
            raise CoverageError(f'No configured SQL files at the {side} revision')
        for i, path in enumerate(paths):
            (target / f'{i + 1:04d}_{PurePosixPath(path).name}').write_text(read(root, revision, path), encoding='utf-8')
    workload = output / 'workload.json'
    workload.write_text(json.dumps(merged), encoding='utf-8')
    return output / 'baseline', output / 'candidate', workload, {'base_commit': base, 'head_commit': head, 'changed_files': changed}
