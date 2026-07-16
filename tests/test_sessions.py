from camoufox_service.sessions import SessionRegistry


def test_session_expiry_uses_monotonic_clock():
    clock = [0.0]
    registry = SessionRegistry(ttl_seconds=10, clock=lambda: clock[0])
    record = registry.create(worker_id=2, session_id="s1")

    assert record.worker_id == 2
    clock[0] = 11.0
    assert registry.expire() == [record]
    assert registry.get("s1") is None


def test_session_get_refreshes_last_used_without_extending_expiry():
    clock = [5.0]
    registry = SessionRegistry(ttl_seconds=10, clock=lambda: clock[0])
    record = registry.create(worker_id=0, session_id="s1")
    clock[0] = 7.0

    found = registry.get("s1")

    assert found is record
    assert found.last_used_at == 7.0
    assert found.expires_at == 15.0

