import json
from pathlib import Path
from querydoctor.benchmark.models import BenchmarkResult
from querydoctor.benchmark.reliability import awards_validation


def test_recorded_showcase_obeys_real_verification_gate():
    root = Path(__file__).resolve().parents[4]
    captures = json.loads((root / 'apps/web/lib/showcaseCaptured.json').read_text(encoding='utf-8'))
    for key, capture in captures.items():
        result = BenchmarkResult.model_validate(capture['benchmark'])
        assert capture['validated'] == awards_validation(result)
        assert result.settings['checksum_algorithm'] == 'sha256'
        assert result.settings['checksum_before'] == result.settings['checksum_after']
        assert len(result.settings['checksum_before'].split(':')[1]) == 64
        assert result.plan_after is not None
        assert result.checksum_match
        assert result.before.n == result.after.n == 7
        assert [s.rows for s in result.before_samples] == [s.rows for s in result.after_samples]
        assert capture['validated'] == (key != 'rejected')
    assert captures['rejected']['comparison']['uses_candidate']
    assert not captures['rejected']['benchmark']['index_used']
