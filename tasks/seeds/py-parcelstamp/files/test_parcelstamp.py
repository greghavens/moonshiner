"""Acceptance gate for the parcel-stamp library.

Run with warnings escalated:  python3 -W error test_parcelstamp.py
"""

import datetime
import io
import re
import ssl
import unittest

import carrier
import checks
import postal
import stamps


def check_postal():
    assert postal.is_zip("02134") is True
    assert postal.is_zip("02134-1021") is True
    assert postal.is_zip("0213") is False
    assert postal.is_zip("021341021") is False
    assert postal.is_route("NE 140") is True
    assert postal.is_route("NE140") is False
    assert postal.is_route("ne 140") is False
    assert postal.split_units("NE 140; NW 022") == ["NE 140", "NW 022"]
    assert postal.split_units("NE 140") == ["NE 140"]


def check_stamps():
    line = stamps.stamp_line("P-0042", "PRIORITY")
    pat = r"P-0042 \[PRIORITY\] printed \d{4}-\d{2}-\d{2} \d{2}:\d{2} UTC"
    assert re.fullmatch(pat, line), "stamp line malformed: %r" % line

    first, last = stamps.pickup_window(5)
    d0 = datetime.date.fromisoformat(first)
    d1 = datetime.date.fromisoformat(last)
    assert (d1 - d0).days == 5, "pickup window span wrong: %s .. %s" % (first, last)


def check_carrier():
    ctx = carrier.client_context()
    assert isinstance(ctx, ssl.SSLContext)
    assert ctx.check_hostname is True, "carrier guide requires hostname checks"
    assert ctx.verify_mode == ssl.VerifyMode.CERT_REQUIRED, ctx.verify_mode
    assert ctx.minimum_version >= ssl.TLSVersion.TLSv1_2, (
        "carrier guide requires TLS 1.2 or newer, context allows %s"
        % ctx.minimum_version
    )


def check_crew_selfcheck():
    suite = unittest.defaultTestLoader.loadTestsFromModule(checks)
    assert suite.countTestCases() >= 3, "crew self-check lost test cases"
    runner = unittest.TextTestRunner(stream=io.StringIO(), verbosity=0)
    result = runner.run(suite)
    assert result.testsRun >= 3, "crew self-check ran %d tests" % result.testsRun
    assert result.wasSuccessful(), (
        "crew self-check not green: %r" % (result.errors + result.failures)
    )


def main():
    check_postal()
    check_stamps()
    check_carrier()
    check_crew_selfcheck()
    print("ok")


if __name__ == "__main__":
    main()
