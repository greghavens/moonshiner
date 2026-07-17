"""Behavior checks for the flag cache. Run: python3 test_flag_cache.py"""
from flag_cache import FlagCache


def test_successful_warmup():
    cache = FlagCache(
        fetch=lambda: {"new-checkout": True, "dark-mode": False},
        defaults={"dark-mode": True, "beta-search": False},
    )
    cache.start()
    assert cache.wait_ready(timeout=5) is True, "warmup did not finish in time"
    assert cache.error() is None
    assert cache.is_enabled("new-checkout") is True
    assert cache.is_enabled("dark-mode") is False, "fetched value must beat default"
    assert cache.is_enabled("beta-search") is False
    assert cache.is_enabled("unknown-flag") is False


def test_flag_service_down():
    def fetch():
        raise ConnectionError("flag service unreachable")

    cache = FlagCache(fetch=fetch, defaults={"dark-mode": True})
    cache.start()
    finished = cache.wait_ready(timeout=2)
    assert finished is True, (
        "warmup attempt was over almost instantly, but wait_ready still "
        "reported a timeout")
    err = cache.error()
    assert isinstance(err, ConnectionError), f"expected the fetch error, got {err!r}"
    assert cache.is_enabled("dark-mode") is True, "defaults must apply after a failed warmup"
    assert cache.is_enabled("new-checkout") is False


def main():
    test_successful_warmup()
    test_flag_service_down()
    print("all checks passed")


if __name__ == "__main__":
    main()
