import fakeredis

from worker.reliability import CancellationChecker, cancel_key, compute_backoff_seconds


def test_backoff_schedule_matches_error_handling_doc():
    # ERROR_HANDLING.md §3: "0.5s -> 1s -> 2s"
    assert compute_backoff_seconds(1, base_seconds=0.5) == 0.5
    assert compute_backoff_seconds(2, base_seconds=0.5) == 1.0
    assert compute_backoff_seconds(3, base_seconds=0.5) == 2.0


def test_cancellation_checker_reads_the_cancel_flag():
    redis_client = fakeredis.FakeRedis(decode_responses=True)
    checker = CancellationChecker(redis_client, "req-1")
    assert checker() is False

    redis_client.set(cancel_key("req-1"), "1")
    assert checker() is True


def test_cancellation_checker_is_scoped_to_its_request_id():
    redis_client = fakeredis.FakeRedis(decode_responses=True)
    redis_client.set(cancel_key("req-other"), "1")
    checker = CancellationChecker(redis_client, "req-1")
    assert checker() is False
