from invoicebatch import InvoiceBatch, TransientError, submit_with_retry


def flaky(fail_times, response):
    """A scripted transport that raises `fail_times` times, then succeeds."""
    state = {"calls": 0}

    def transport(payload):
        state["calls"] += 1
        if state["calls"] <= fail_times:
            raise TransientError(f"drop {state['calls']}")
        return response

    transport.state = state
    return transport


def check_instance_isolation():
    first = InvoiceBatch("A-1", invoices=[{"id": "inv-1", "amount_cents": 5000}])
    second = InvoiceBatch("A-2")
    first.add_adjustment("late-fee", 250)
    first.add_adjustment("late-fee", 250)
    assert first.total_cents() == 5500
    assert second.adjustments == []
    assert second.total_cents() == 0
    third = InvoiceBatch("A-3")
    assert third.adjustments == []


def check_caller_list_is_copied():
    seed = [{"code": "promo", "amount_cents": -100}]
    batch = InvoiceBatch("B-1", adjustments=seed)
    batch.add_adjustment("rush", 400)
    assert seed == [{"code": "promo", "amount_cents": -100}]
    other = InvoiceBatch("B-2", adjustments=seed)
    assert other.total_cents() == -100
    assert batch.total_cents() == 300


def check_payload_is_snapshot():
    batch = InvoiceBatch("C-1", invoices=[{"id": "inv-9", "amount_cents": 700}])
    payload = batch.to_payload()
    payload["adjustments"].append({"code": "oops", "amount_cents": 999})
    payload["invoices"].append({"id": "ghost", "amount_cents": 1})
    assert batch.total_cents() == 700
    assert batch.to_payload()["adjustments"] == []
    before = batch.serialize()
    batch.add_adjustment("credit", -200)
    assert batch.serialize() != before
    assert batch.total_cents() == 500


def check_serialization_is_stable():
    batch = InvoiceBatch("B-9", invoices=[{"amount_cents": 1250, "id": "inv-1"}])
    batch.add_adjustment("goodwill", -250)
    expected = (
        '{"adjustments":[{"amount_cents":-250,"code":"goodwill"}],'
        '"batch_id":"B-9",'
        '"invoices":[{"amount_cents":1250,"id":"inv-1"}],'
        '"total_cents":1000}'
    )
    assert batch.serialize() == expected


def check_retry_with_explicit_logs():
    one = InvoiceBatch("B-1")
    log_one = []
    t1 = flaky(2, "ok:B-1")
    assert submit_with_retry(one, t1, attempt_log=log_one) == "ok:B-1"
    assert log_one == [("B-1", 1), ("B-1", 2), ("B-1", 3)]
    assert t1.state["calls"] == 3

    two = InvoiceBatch("B-2")
    log_two = []
    t2 = flaky(1, "ok:B-2")
    assert submit_with_retry(two, t2, attempt_log=log_two) == "ok:B-2"
    assert log_two == [("B-2", 1), ("B-2", 2)]


def check_retry_with_reused_caller_log():
    shared_log = []
    x = InvoiceBatch("X-1")
    y = InvoiceBatch("Y-1")
    assert submit_with_retry(x, flaky(0, "ok:x"), attempt_log=shared_log) == "ok:x"
    assert submit_with_retry(y, flaky(0, "ok:y"), attempt_log=shared_log) == "ok:y"
    assert shared_log == [("X-1", 1), ("Y-1", 1)]


def check_retry_budget_is_per_call():
    # Three submissions in one process, each needing all three attempts.
    for n in range(3):
        batch = InvoiceBatch(f"D-{n}")
        transport = flaky(2, f"ok:D-{n}")
        assert submit_with_retry(batch, transport) == f"ok:D-{n}"
        assert transport.state["calls"] == 3


def check_retry_exhaustion():
    batch = InvoiceBatch("E-1")
    transport = flaky(99, "never")
    log = []
    try:
        submit_with_retry(batch, transport, max_attempts=3, attempt_log=log)
        raise AssertionError("expected TransientError")
    except TransientError:
        pass
    assert transport.state["calls"] == 3
    assert log == [("E-1", 1), ("E-1", 2), ("E-1", 3)]


def main():
    check_instance_isolation()
    check_caller_list_is_copied()
    check_payload_is_snapshot()
    check_serialization_is_stable()
    check_retry_with_explicit_logs()
    check_retry_with_reused_caller_log()
    check_retry_budget_is_per_call()
    check_retry_exhaustion()
    print("all invoicebatch tests passed")


if __name__ == "__main__":
    main()
