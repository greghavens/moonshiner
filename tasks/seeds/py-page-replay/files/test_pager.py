from pager import RecordPager, TransientFetchError


class ScriptedService:
    """A fake page API driven entirely by a scripted page table."""

    def __init__(self, pages, fail_on=()):
        self.pages = pages  # cursor -> (items, next_cursor)
        self.fail_on = set(fail_on)  # 1-based fetch call numbers that blip
        self.calls = []

    def fetch(self, cursor):
        self.calls.append(cursor)
        if len(self.calls) in self.fail_on:
            raise TransientFetchError(f"blip on call {len(self.calls)}")
        items, nxt = self.pages[cursor]
        return {"items": [dict(r) for r in items], "next": nxt}


def rec(rid):
    return {"id": rid, "kind": "shipment"}


def three_pages():
    return {
        None: ([rec("r-01"), rec("r-02")], "c-7f3a"),
        "c-7f3a": ([rec("r-03"), rec("r-04")], "c-1be9"),
        "c-1be9": ([rec("r-05"), rec("r-06")], None),
    }


ALL_IDS = ["r-01", "r-02", "r-03", "r-04", "r-05", "r-06"]


def ids(pager):
    return [r["id"] for r in pager]


def check_clean_run():
    svc = ScriptedService(three_pages())
    assert ids(RecordPager(svc.fetch)) == ALL_IDS
    assert svc.calls == [None, "c-7f3a", "c-1be9"]


def check_blip_on_first_fetch():
    svc = ScriptedService(three_pages(), fail_on={1})
    assert ids(RecordPager(svc.fetch)) == ALL_IDS


def check_blip_mid_stream():
    svc = ScriptedService(three_pages(), fail_on={2})
    assert ids(RecordPager(svc.fetch)) == ALL_IDS
    # The retry must re-request the cursor that failed, verbatim.
    assert svc.calls[1] == "c-7f3a"
    assert svc.calls[2] == "c-7f3a"


def check_double_blip_same_page():
    svc = ScriptedService(three_pages(), fail_on={2, 3})
    assert ids(RecordPager(svc.fetch, max_retries=2)) == ALL_IDS


def check_blips_on_separate_pages():
    svc = ScriptedService(three_pages(), fail_on={2, 4})
    # Each page gets its own retry budget.
    assert ids(RecordPager(svc.fetch, max_retries=1)) == ALL_IDS


def check_empty_page_with_continuation():
    pages = {
        None: ([rec("a-1")], "c-empty"),
        "c-empty": ([], "c-tail"),
        "c-tail": ([rec("a-2"), rec("a-3")], None),
    }
    svc = ScriptedService(pages, fail_on={2})
    assert ids(RecordPager(svc.fetch)) == ["a-1", "a-2", "a-3"]


def check_retry_limit_exhausted():
    svc = ScriptedService(three_pages(), fail_on={2, 3})
    got = []
    try:
        for r in RecordPager(svc.fetch, max_retries=1):
            got.append(r["id"])
        raise AssertionError("expected TransientFetchError")
    except TransientFetchError:
        pass
    assert got == ["r-01", "r-02"]
    assert len(svc.calls) == 3


def main():
    check_clean_run()
    check_blip_on_first_fetch()
    check_blip_mid_stream()
    check_double_blip_same_page()
    check_blips_on_separate_pages()
    check_empty_page_with_continuation()
    check_retry_limit_exhausted()
    print("all pager tests passed")


if __name__ == "__main__":
    main()
