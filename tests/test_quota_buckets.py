import pytest

import db
import quota


def budget(conn, limit=100):
    b = quota.SearchBudget(conn)
    b.limit = limit
    return b


def log(conn, n, bucket):
    for _ in range(n):
        db.log_search_call(conn, bucket)


def test_a_capped_bucket_stops_at_its_share_without_touching_the_real_limit(conn):
    b = budget(conn)
    b.set_bucket("new", 0.8)
    log(conn, 80, "new")
    with pytest.raises(quota.BucketExhausted):
        b.check()
    b.finish()
    assert b.limit == 100                       # a bucket cap says nothing about Spotify's real ceiling


def test_the_limit_is_fixed_and_does_not_rise_after_using_it_up(conn):
    b = quota.SearchBudget(conn)
    assert b.limit == quota.CALL_LIMIT == 650
    b.set_bucket("verify", None)
    log(conn, quota.CALL_LIMIT, "new")
    with pytest.raises(quota.QuotaExhausted) as excinfo:
        b.check()
    assert not isinstance(excinfo.value, quota.BucketExhausted)
    b.finish()
    assert quota.SearchBudget(conn).limit == quota.CALL_LIMIT


def test_what_the_capped_bucket_left_unused_flows_to_the_uncapped_one(conn):
    b = budget(conn)
    log(conn, 10, "new")                        # the 80% bucket only used 10 of its 80
    b.set_bucket("verify", None)
    log(conn, 89, "verify")
    b.check()                                   # 99 of 100 used: still allowed
    b.record_call()
    with pytest.raises(quota.QuotaExhausted):
        b.check()


def test_calls_are_tagged_with_their_bucket(conn):
    b = budget(conn)
    b.set_bucket("new", 0.8)
    b.record_call()
    b.set_bucket("verify", None)
    b.record_call()
    b.record_call()
    assert db.search_calls_in_last_24h(conn, "new") == 1
    assert db.search_calls_in_last_24h(conn, "verify") == 2
    assert db.search_calls_in_last_24h(conn) == 3


def test_a_real_block_stops_searching_but_does_not_change_the_limit(conn):
    b = quota.SearchBudget(conn)
    log(conn, 10, "new")
    b.record_block(3600)
    assert b.limit == quota.CALL_LIMIT and b.blocked
    with pytest.raises(quota.QuotaBlocked):
        quota.SearchBudget(conn).check()        # the block survives into the next run
