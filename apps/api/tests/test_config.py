from app.config import Settings


def test_small_scale_lowers_the_seq_scan_threshold_and_full_scale_keeps_the_default():
    assert Settings(scale="small").diagnosis_thresholds().seq_scan_min_rows == 50_000
    assert Settings(scale="full").diagnosis_thresholds().seq_scan_min_rows == 100_000


def test_explicit_threshold_override_wins():
    assert Settings(scale="small", seq_scan_min_rows=1_234).diagnosis_thresholds().seq_scan_min_rows == 1_234


def test_celery_tasks_have_hard_time_limits_so_one_pathological_job_cannot_hold_a_worker_forever():
    from app.workers.celery_app import celery_app

    assert 0 < celery_app.conf.task_soft_time_limit < celery_app.conf.task_time_limit <= 600
