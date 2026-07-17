# Acceptance tests for the s3mpu multipart uploader.
#
# The uploader is exercised through a real boto3 S3 client wrapped in
# botocore's Stubber, so every request is validated against the actual S3
# service model. No network, no real credentials.
import json
from pathlib import Path

import boto3
import pytest
from botocore.config import Config
from botocore.stub import Stubber

import s3mpu

CONTRACT = json.loads((Path(__file__).parent / "docs" / "contract.json").read_text())
CONST = CONTRACT["constants"]

BUCKET = "telemetry-archive-staging"
KEY = "rollups/2026/07/16/device-batch.bin"
MIB = 1024 * 1024


def make_client():
    # Dummy credentials; total_max_attempts=1 disables SDK-internal retries so
    # the uploader's own retry policy is what these tests observe.
    return boto3.client(
        "s3",
        region_name="us-east-1",
        aws_access_key_id="TEST_ACCESS_KEY_ID",
        aws_secret_access_key="dummy-secret-never-real",
        config=Config(retries={"total_max_attempts": 1}),
    )


def make_uploader(client, **kw):
    kw.setdefault("threshold", 512)
    kw.setdefault("part_size", 400)
    kw.setdefault("min_part_size", 256)
    kw.setdefault("base_delay", 0.5)
    kw.setdefault("max_delay", 8.0)
    kw.setdefault("max_attempts", 4)
    return s3mpu.MultipartUploader(client, BUCKET, **kw)


def stub_multipart_start(stubber, chunks, upload_id="upl-8f31ac"):
    stubber.add_response(
        "create_multipart_upload",
        {"UploadId": upload_id},
        {"Bucket": BUCKET, "Key": KEY, "ChecksumAlgorithm": "CRC32"},
    )
    return upload_id


def part_response(i):
    return {"ETag": f'"etag-part-{i}"', "ChecksumCRC32": f"crc{i}AA=="}


def expected_part(i, chunk, upload_id):
    return {
        "Bucket": BUCKET,
        "Key": KEY,
        "UploadId": upload_id,
        "PartNumber": i,
        "Body": chunk,
    }


def completed_parts(n):
    return [
        {"ETag": f'"etag-part-{i}"', "PartNumber": i, "ChecksumCRC32": f"crc{i}AA=="}
        for i in range(1, n + 1)
    ]


# ---------------------------------------------------------------- constants


def test_documented_constants_are_pinned():
    assert s3mpu.MIN_PART_SIZE == CONST["min_part_size_bytes"]
    assert s3mpu.DEFAULT_MULTIPART_THRESHOLD == CONST["multipart_threshold_bytes"]
    assert s3mpu.MAX_PARTS == CONST["max_parts"]
    assert set(s3mpu.RETRYABLE_ERROR_CODES) == set(CONTRACT["retryable_error_codes"])
    for code in CONTRACT["terminal_error_codes"]:
        assert code not in s3mpu.RETRYABLE_ERROR_CODES


def test_strategy_selection_uses_threshold():
    t = CONST["multipart_threshold_bytes"]
    assert s3mpu.select_strategy(t - 1) == "single"
    assert s3mpu.select_strategy(t) == "multipart"
    assert s3mpu.select_strategy(t + 1) == "multipart"
    assert s3mpu.select_strategy(1, threshold=512) == "single"
    assert s3mpu.select_strategy(512, threshold=512) == "multipart"


# ------------------------------------------------------------------- plans


def test_plan_parts_is_deterministic_and_ordered():
    plan = s3mpu.plan_parts(17 * MIB, 5 * MIB)
    assert [p.number for p in plan] == [1, 2, 3, 4]
    assert [p.offset for p in plan] == [0, 5 * MIB, 10 * MIB, 15 * MIB]
    assert [p.size for p in plan] == [5 * MIB, 5 * MIB, 5 * MIB, 2 * MIB]
    assert sum(p.size for p in plan) == 17 * MIB

    exact = s3mpu.plan_parts(10 * MIB, 5 * MIB)
    assert [(p.number, p.size) for p in exact] == [(1, 5 * MIB), (2, 5 * MIB)]


def test_plan_parts_rejects_undersized_parts_and_overflow():
    with pytest.raises(ValueError):
        s3mpu.plan_parts(17 * MIB, 4 * MIB)  # below the documented 5 MiB floor
    with pytest.raises(ValueError):
        s3mpu.plan_parts(0, 5 * MIB)
    with pytest.raises(ValueError):
        # 10,001 full parts would exceed the documented 10,000-part maximum.
        s3mpu.plan_parts(5 * MIB * (CONST["max_parts"] + 1), 5 * MIB)
    # Exactly 10,000 parts is legal.
    plan = s3mpu.plan_parts(5 * MIB * CONST["max_parts"], 5 * MIB)
    assert len(plan) == CONST["max_parts"]
    assert plan[-1].number == CONST["max_parts"]


# ------------------------------------------------------------- single put


def test_small_object_uses_single_put():
    client = make_client()
    up = make_uploader(client)
    body = b"z" * 100
    with Stubber(client) as stub:
        stub.add_response(
            "put_object",
            {"ETag": '"9a0364b9e99bb480dd25e1f0284c8555"', "ChecksumCRC32": "oD2/ug=="},
            {"Bucket": BUCKET, "Key": KEY, "Body": body, "ChecksumAlgorithm": "CRC32"},
        )
        result = up.upload(KEY, body)
        stub.assert_no_pending_responses()
    assert result.strategy == "single"
    assert result.upload_id is None
    assert result.parts == []
    assert result.etag == '"9a0364b9e99bb480dd25e1f0284c8555"'
    assert result.checksum_crc32 == "oD2/ug=="


def test_single_put_retries_slowdown_then_succeeds():
    client = make_client()
    delays = []
    up = make_uploader(client, sleep=delays.append)
    body = b"q" * 64
    expected = {"Bucket": BUCKET, "Key": KEY, "Body": body, "ChecksumAlgorithm": "CRC32"}
    with Stubber(client) as stub:
        stub.add_client_error(
            "put_object",
            service_error_code="SlowDown",
            service_message="Reduce your request rate.",
            http_status_code=503,
            expected_params=expected,
        )
        stub.add_response("put_object", {"ETag": '"ok"'}, expected)
        result = up.upload(KEY, body)
        stub.assert_no_pending_responses()
    assert result.etag == '"ok"'
    assert delays == [0.5]


# -------------------------------------------------------------- multipart


def test_multipart_flow_uploads_parts_in_order_and_completes():
    client = make_client()
    up = make_uploader(client)
    chunks = [b"a" * 400, b"b" * 400, b"c" * 200]
    body = b"".join(chunks)
    with Stubber(client) as stub:
        uid = stub_multipart_start(stub, chunks)
        for i, chunk in enumerate(chunks, start=1):
            stub.add_response("upload_part", part_response(i), expected_part(i, chunk, uid))
        stub.add_response(
            "complete_multipart_upload",
            {
                "Bucket": BUCKET,
                "Key": KEY,
                "ETag": '"9b2cf535f27731c974343645a3985328-3"',
                "ChecksumCRC32": "mNo3Qw==-3",
                "ChecksumType": "COMPOSITE",
            },
            {
                "Bucket": BUCKET,
                "Key": KEY,
                "UploadId": uid,
                "MultipartUpload": {"Parts": completed_parts(3)},
            },
        )
        result = up.upload(KEY, body)
        stub.assert_no_pending_responses()
    assert result.strategy == "multipart"
    assert result.upload_id == uid
    assert [p.number for p in result.parts] == [1, 2, 3]
    assert [p.etag for p in result.parts] == ['"etag-part-1"', '"etag-part-2"', '"etag-part-3"']
    assert [p.checksum_crc32 for p in result.parts] == ["crc1AA==", "crc2AA==", "crc3AA=="]
    assert result.etag == '"9b2cf535f27731c974343645a3985328-3"'
    assert result.etag.strip('"').endswith("-3")  # documented multipart ETag shape
    assert result.checksum_crc32 == "mNo3Qw==-3"


def test_retryable_part_error_is_retried_and_upload_succeeds():
    client = make_client()
    delays = []
    up = make_uploader(client, sleep=delays.append)
    chunks = [b"a" * 400, b"b" * 400, b"c" * 200]
    with Stubber(client) as stub:
        uid = stub_multipart_start(stub, chunks)
        stub.add_response("upload_part", part_response(1), expected_part(1, chunks[0], uid))
        stub.add_client_error(
            "upload_part",
            service_error_code="SlowDown",
            service_message="Reduce your request rate.",
            http_status_code=503,
            expected_params=expected_part(2, chunks[1], uid),
        )
        stub.add_response("upload_part", part_response(2), expected_part(2, chunks[1], uid))
        stub.add_response("upload_part", part_response(3), expected_part(3, chunks[2], uid))
        stub.add_response(
            "complete_multipart_upload",
            {"ETag": '"agg-3"', "ChecksumType": "COMPOSITE"},
            {
                "Bucket": BUCKET,
                "Key": KEY,
                "UploadId": uid,
                "MultipartUpload": {"Parts": completed_parts(3)},
            },
        )
        result = up.upload(KEY, b"".join(chunks))
        stub.assert_no_pending_responses()
    assert delays == [0.5]
    assert [p.number for p in result.parts] == [1, 2, 3]


def test_backoff_schedule_doubles_and_caps_then_aborts():
    client = make_client()
    delays = []
    up = make_uploader(client, sleep=delays.append, max_attempts=4, base_delay=0.5, max_delay=1.0)
    chunks = [b"a" * 400, b"b" * 160]
    with Stubber(client) as stub:
        uid = stub_multipart_start(stub, chunks)
        for _ in range(4):
            stub.add_client_error(
                "upload_part",
                service_error_code="RequestTimeout",
                service_message="Your socket connection to the server was not read from.",
                http_status_code=400,
                expected_params=expected_part(1, chunks[0], uid),
            )
        stub.add_response(
            "abort_multipart_upload",
            {},
            {"Bucket": BUCKET, "Key": KEY, "UploadId": uid},
        )
        with pytest.raises(s3mpu.UploadFailed) as exc:
            up.upload(KEY, b"".join(chunks))
        stub.assert_no_pending_responses()
    # min(max_delay, base * 2**(attempt-1)) between the 4 attempts: 0.5, 1.0, 1.0
    assert delays == [0.5, 1.0, 1.0]
    assert exc.value.key == KEY
    assert exc.value.upload_id == uid
    assert exc.value.error_code == "RequestTimeout"


def test_terminal_error_aborts_immediately_without_retry():
    client = make_client()
    delays = []
    up = make_uploader(client, sleep=delays.append)
    chunks = [b"a" * 400, b"b" * 160]
    with Stubber(client) as stub:
        uid = stub_multipart_start(stub, chunks)
        stub.add_client_error(
            "upload_part",
            service_error_code="AccessDenied",
            service_message="Access Denied",
            http_status_code=403,
            expected_params=expected_part(1, chunks[0], uid),
        )
        stub.add_response(
            "abort_multipart_upload",
            {},
            {"Bucket": BUCKET, "Key": KEY, "UploadId": uid},
        )
        with pytest.raises(s3mpu.UploadFailed) as exc:
            up.upload(KEY, b"".join(chunks))
        stub.assert_no_pending_responses()
    assert delays == []  # terminal codes must not burn retry attempts
    assert exc.value.error_code == "AccessDenied"


def test_invalid_part_on_complete_aborts_and_reports():
    client = make_client()
    up = make_uploader(client)
    chunks = [b"a" * 400, b"b" * 160]
    with Stubber(client) as stub:
        uid = stub_multipart_start(stub, chunks)
        stub.add_response("upload_part", part_response(1), expected_part(1, chunks[0], uid))
        stub.add_response("upload_part", part_response(2), expected_part(2, chunks[1], uid))
        stub.add_client_error(
            "complete_multipart_upload",
            service_error_code="InvalidPart",
            service_message="One or more of the specified parts could not be found.",
            http_status_code=400,
            expected_params={
                "Bucket": BUCKET,
                "Key": KEY,
                "UploadId": uid,
                "MultipartUpload": {"Parts": completed_parts(2)},
            },
        )
        stub.add_response(
            "abort_multipart_upload",
            {},
            {"Bucket": BUCKET, "Key": KEY, "UploadId": uid},
        )
        with pytest.raises(s3mpu.UploadFailed) as exc:
            up.upload(KEY, b"".join(chunks))
        stub.assert_no_pending_responses()
    assert exc.value.error_code == "InvalidPart"
    assert exc.value.upload_id == uid


def test_failed_abort_still_raises_original_error():
    client = make_client()
    up = make_uploader(client)
    chunks = [b"a" * 400, b"b" * 160]
    with Stubber(client) as stub:
        uid = stub_multipart_start(stub, chunks)
        stub.add_client_error(
            "upload_part",
            service_error_code="AccessDenied",
            service_message="Access Denied",
            http_status_code=403,
            expected_params=expected_part(1, chunks[0], uid),
        )
        stub.add_client_error(
            "abort_multipart_upload",
            service_error_code="NoSuchUpload",
            service_message="The specified upload does not exist.",
            http_status_code=404,
            expected_params={"Bucket": BUCKET, "Key": KEY, "UploadId": uid},
        )
        with pytest.raises(s3mpu.UploadFailed) as exc:
            up.upload(KEY, b"".join(chunks))
        stub.assert_no_pending_responses()
    # The abort failure must not mask what actually killed the upload.
    assert exc.value.error_code == "AccessDenied"


def test_content_type_is_forwarded_to_create_and_single_put():
    client = make_client()
    up = make_uploader(client)
    body = b"x" * 10
    with Stubber(client) as stub:
        stub.add_response(
            "put_object",
            {"ETag": '"ct"'},
            {
                "Bucket": BUCKET,
                "Key": KEY,
                "Body": body,
                "ChecksumAlgorithm": "CRC32",
                "ContentType": "application/octet-stream",
            },
        )
        up.upload(KEY, body, content_type="application/octet-stream")
        stub.assert_no_pending_responses()

    client2 = make_client()
    up2 = make_uploader(client2)
    chunks = [b"a" * 400, b"b" * 160]
    with Stubber(client2) as stub:
        stub.add_response(
            "create_multipart_upload",
            {"UploadId": "upl-ct"},
            {
                "Bucket": BUCKET,
                "Key": KEY,
                "ChecksumAlgorithm": "CRC32",
                "ContentType": "application/octet-stream",
            },
        )
        stub.add_response("upload_part", part_response(1), expected_part(1, chunks[0], "upl-ct"))
        stub.add_response("upload_part", part_response(2), expected_part(2, chunks[1], "upl-ct"))
        stub.add_response(
            "complete_multipart_upload",
            {"ETag": '"agg-2"'},
            {
                "Bucket": BUCKET,
                "Key": KEY,
                "UploadId": "upl-ct",
                "MultipartUpload": {"Parts": completed_parts(2)},
            },
        )
        up2.upload(KEY, b"".join(chunks), content_type="application/octet-stream")
        stub.assert_no_pending_responses()


def test_uploader_defaults_match_documented_values():
    client = make_client()
    up = s3mpu.MultipartUploader(client, BUCKET)
    assert up.threshold == CONST["multipart_threshold_bytes"]
    assert up.min_part_size == CONST["min_part_size_bytes"]
