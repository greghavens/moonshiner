"""Acceptance tests for the signed-webhook-delivery verifier.

Run: python3 test_webhook_verify.py
"""
import hashlib
import hmac as hmac_mod


NOW = 1_700_000_000  # every scenario pins the clock here unless it says otherwise


def clock_at(t):
    return lambda: t


def expected_hex(secret, body, timestamp):
    payload = str(timestamp).encode("utf-8") + b"." + body
    return hmac_mod.new(secret.encode("utf-8"), payload, hashlib.sha256).hexdigest()


def main():
    from webhook_verify import WebhookVerifier, sign

    body = b'{"event":"invoice.paid","id":"inv_42"}'

    # -- sign() produces the exact documented header format --
    hdr = sign("whsec_alpha", body, NOW)
    assert hdr == "t=%d,v1=%s" % (NOW, expected_hex("whsec_alpha", body, NOW)), hdr

    # -- a well-signed, fresh delivery is accepted --
    v = WebhookVerifier(["whsec_alpha"], clock=clock_at(NOW))
    r = v.verify(body, hdr)
    assert r.accepted is True and r.reason is None, (r.accepted, r.reason)

    # -- a delivery whose body does not match its signature is declined --
    r = v.verify(b'{"event":"invoice.paid","id":"inv_43"}', hdr)
    assert r.accepted is False and r.reason == "no_matching_signature", r.reason

    # -- signature hex comparison is case-insensitive --
    up = "t=%d,v1=%s" % (NOW, expected_hex("whsec_alpha", body, NOW).upper())
    r = v.verify(body, up)
    assert r.accepted is True, r.reason

    # -- the timestamp is bound into the signed payload: swapping in a
    #    different in-window t must not verify --
    swapped = "t=%d,v1=%s" % (NOW - 10, expected_hex("whsec_alpha", body, NOW))
    r = v.verify(body, swapped)
    assert r.accepted is False and r.reason == "no_matching_signature", r.reason

    # -- tolerance window: exactly at the edge passes, one past is declined,
    #    symmetric in both directions (default tolerance is 300s) --
    v = WebhookVerifier(["whsec_alpha"], clock=clock_at(NOW))
    assert v.verify(body, sign("whsec_alpha", body, NOW - 300)).accepted is True
    assert v.verify(body, sign("whsec_alpha", body, NOW + 300)).accepted is True
    r = v.verify(body, sign("whsec_alpha", body, NOW - 301))
    assert r.accepted is False and r.reason == "stale_timestamp", r.reason
    r = v.verify(body, sign("whsec_alpha", body, NOW + 301))
    assert r.accepted is False and r.reason == "stale_timestamp", r.reason

    # -- custom tolerance is honored --
    tight = WebhookVerifier(["whsec_alpha"], tolerance=60, clock=clock_at(NOW))
    assert tight.verify(body, sign("whsec_alpha", body, NOW - 60)).accepted is True
    assert tight.verify(body, sign("whsec_alpha", body, NOW - 61)).reason == "stale_timestamp"

    # -- a stale timestamp wins over a mismatched signature (fixed check order) --
    stale_and_wrong = "t=%d,v1=%s" % (NOW - 9999, "ab" * 32)
    r = v.verify(body, stale_and_wrong)
    assert r.reason == "stale_timestamp", r.reason

    # -- secret rotation: any configured secret may match --
    rot = WebhookVerifier(["whsec_new", "whsec_old"], clock=clock_at(NOW))
    assert rot.verify(body, sign("whsec_old", body, NOW)).accepted is True
    assert rot.verify(body, sign("whsec_new", body, NOW)).accepted is True
    r = rot.verify(body, sign("whsec_retired", body, NOW))
    assert r.accepted is False and r.reason == "no_matching_signature", r.reason

    # -- multiple v1 entries: any one matching is enough --
    multi = "t=%d,v1=%s,v1=%s" % (
        NOW, "00" * 32, expected_hex("whsec_new", body, NOW))
    assert rot.verify(body, multi).accepted is True

    # -- unknown keys (v0 et al.) are ignored, not treated as malformed --
    with_v0 = "t=%d,v0=legacy-junk,v1=%s" % (NOW, expected_hex("whsec_new", body, NOW))
    assert rot.verify(body, with_v0).accepted is True

    # -- whitespace after the comma split is tolerated --
    spaced = "t=%d, v1=%s" % (NOW, expected_hex("whsec_new", body, NOW))
    assert rot.verify(body, spaced).accepted is True

    # -- malformed headers are declined as bad_header --
    good_sig = expected_hex("whsec_alpha", body, NOW)
    for bad in (
        None,                                   # header missing entirely
        "",                                     # empty
        "v1=" + good_sig,                       # no timestamp
        "t=%d" % NOW,                           # no v1 entry
        "t=%d,t=%d,v1=%s" % (NOW, NOW, good_sig),  # repeated t
        "t=notanumber,v1=" + good_sig,          # non-integer t
        "t=%d,justgarbage" % NOW,               # item without '='
    ):
        r = v.verify(body, bad)
        assert r.accepted is False and r.reason == "bad_header", (bad, r.reason)

    # -- bad_header wins over everything else in the fixed order --
    r = v.verify(body, "v1=deadbeef")
    assert r.reason == "bad_header", r.reason

    # -- replay cache: a delivery id already seen is declined --
    v = WebhookVerifier(["whsec_alpha"], clock=clock_at(NOW))
    h = sign("whsec_alpha", body, NOW)
    assert v.verify(body, h, delivery_id="dlv_1").accepted is True
    r = v.verify(body, h, delivery_id="dlv_1")
    assert r.accepted is False and r.reason == "duplicate", r.reason
    # a different id sails through
    assert v.verify(body, h, delivery_id="dlv_2").accepted is True

    # -- without a delivery id the replay check is skipped entirely --
    assert v.verify(body, h).accepted is True
    assert v.verify(body, h).accepted is True

    # -- declined deliveries do not record their id --
    v = WebhookVerifier(["whsec_alpha"], clock=clock_at(NOW))
    bad_sig = "t=%d,v1=%s" % (NOW, "11" * 32)
    r = v.verify(body, bad_sig, delivery_id="dlv_retry")
    assert r.accepted is False and r.reason == "no_matching_signature"
    # same id, now correctly signed: must be accepted, not flagged duplicate
    r = v.verify(body, sign("whsec_alpha", body, NOW), delivery_id="dlv_retry")
    assert r.accepted is True, r.reason

    # -- cache eviction: oldest-recorded id falls out first --
    v = WebhookVerifier(["whsec_alpha"], clock=clock_at(NOW), cache_size=2)
    h = sign("whsec_alpha", body, NOW)
    assert v.verify(body, h, delivery_id="a").accepted is True
    assert v.verify(body, h, delivery_id="b").accepted is True
    assert v.verify(body, h, delivery_id="c").accepted is True   # evicts "a"
    assert v.verify(body, h, delivery_id="c").reason == "duplicate"
    r = v.verify(body, h, delivery_id="a")                       # evicted → accepted again
    assert r.accepted is True, r.reason
    # re-accepting "a" evicted "b" (oldest recorded); "c" is still remembered
    assert v.verify(body, h, delivery_id="c").reason == "duplicate"
    assert v.verify(body, h, delivery_id="b").accepted is True   # evicts "c"
    assert v.verify(body, h, delivery_id="a").reason == "duplicate"

    # -- constructor rejects an empty secret list --
    try:
        WebhookVerifier([], clock=clock_at(NOW))
    except ValueError:
        pass
    else:
        raise AssertionError("empty secrets list must raise ValueError")

    print("ok")


if __name__ == "__main__":
    main()
