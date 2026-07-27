"""Release intake policy boundary used by the offline fixture."""


def authenticate(request):
    return request.get("authenticated") is True


def verify_signature(manifest):
    return manifest.get("signature") == "valid"


def record_apply(manifest):
    return {"release": manifest["release"], "applied": True}


def apply_bundle(manifest):
    return record_apply(manifest)


def validate_manifest(manifest, require_signature):
    signature = manifest.get("signature")
    if require_signature and signature is None:
        return False, "signature_required"
    if require_signature and not verify_signature(manifest):
        return False, "invalid_signature"
    return True, "eligible"


def http_ingest(request, policy):
    if not authenticate(request):
        return {"accepted": False, "applied": False, "reason": "authentication_required"}
    if not policy["PUBLIC_INGEST"]:
        return {"accepted": False, "applied": False, "reason": "public_ingest_disabled"}

    valid, reason = validate_manifest(request["manifest"], policy["REQUIRE_SIGNATURE"])
    if not valid:
        return {"accepted": False, "applied": False, "reason": reason}

    apply_bundle(request["manifest"])
    return {"accepted": True, "applied": True, "reason": "accepted"}
