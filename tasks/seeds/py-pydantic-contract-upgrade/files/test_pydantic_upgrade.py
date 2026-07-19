from datetime import datetime, timezone
from pathlib import Path

from orders.payload import OrderPayload
from pydantic_v2 import PydanticUserError, ValidationError
from shipping.publisher import OrderPublisher


ROOT = Path(__file__).resolve().parent
passed = 0
failed = 0


def check(name, body):
    global passed, failed
    try:
        body()
        passed += 1
        print(f"PASS {name}")
    except Exception as error:
        failed += 1
        print(f"FAIL {name}: {type(error).__name__}: {error}")


def wire_payload(**updates):
    value = {
        "orderId": "ORD-7",
        "itemCode": " part-red ",
        "quantity": 3,
        "requestedAt": datetime(2026, 9, 3, 14, 5, 6, tzinfo=timezone.utc),
        "expedited": True,
    }
    value.update(updates)
    return value


def current_hooks_validate_and_publish_the_golden_wire_shape():
    sent = []
    body = OrderPublisher(sent.append).publish(wire_payload())
    expected = (ROOT / "fixtures/order_wire.json").read_bytes().rstrip(b"\n")
    assert body == expected
    assert sent == [expected]


def aliases_and_field_names_are_both_supported_without_output_drift():
    by_name = OrderPayload.model_validate(
        {
            "order_id": "ORD-8",
            "item_code": " blue-9 ",
            "quantity": 2,
            "requested_at": datetime(2026, 10, 1, 8, 0, tzinfo=timezone.utc),
        }
    )
    assert by_name.item_code == "BLUE-9"
    assert by_name.expedited is False
    assert by_name.model_dump(by_alias=True, mode="json") == {
        "orderId": "ORD-8",
        "itemCode": "BLUE-9",
        "quantity": 2,
        "requestedAt": "2026-10-01T08:00:00Z",
        "expedited": False,
    }

    decoded = OrderPayload.model_validate_json(
        (ROOT / "fixtures/order_wire.json").read_text()
    )
    assert decoded.requested_at == datetime(2026, 9, 3, 14, 5, 6, tzinfo=timezone.utc)
    assert decoded.model_dump_json(by_alias=True) == (
        ROOT / "fixtures/order_wire.json"
    ).read_text().strip()


def error_types_and_public_locations_remain_stable():
    cases = [
        (wire_payload(quantity="3"), ("quantity",), "int_type"),
        (wire_payload(quantity=True), ("quantity",), "int_type"),
        (wire_payload(expedited="true"), ("expedited",), "bool_type"),
        ({key: value for key, value in wire_payload().items() if key != "itemCode"}, ("itemCode",), "missing"),
        (wire_payload(debug=True), ("debug",), "extra_forbidden"),
    ]
    for payload, location, issue_type in cases:
        try:
            OrderPayload.model_validate(payload)
            raise AssertionError(f"invalid input unexpectedly passed: {payload!r}")
        except ValidationError as error:
            issue = error.errors()[0]
            assert issue["loc"] == location, issue
            assert issue["type"] == issue_type, issue


def field_and_model_validators_keep_their_errors_and_locations():
    for quantity in (0, -4):
        try:
            OrderPayload.model_validate(wire_payload(quantity=quantity))
            raise AssertionError("non-positive quantity passed")
        except ValidationError as error:
            assert error.errors()[0]["loc"] == ("quantity",)
            assert error.errors()[0]["msg"] == "quantity must be positive"

    try:
        OrderPayload.model_validate(wire_payload(quantity=11, expedited=True))
        raise AssertionError("oversized expedited order passed")
    except ValidationError as error:
        assert error.errors()[0]["loc"] == ()
        assert error.errors()[0]["msg"] == "expedited quantity cannot exceed 10"


def validation_failures_never_publish_and_are_not_swallowed():
    sent = []
    publisher = OrderPublisher(sent.append)
    try:
        publisher.publish(wire_payload(quantity="3"))
        raise AssertionError("invalid payload published")
    except ValidationError:
        pass
    assert sent == []

    def failing_transport(_body):
        raise RuntimeError("fixture transport rejected write")

    try:
        OrderPublisher(failing_transport).publish(wire_payload())
        raise AssertionError("transport failure was swallowed")
    except RuntimeError as error:
        assert str(error) == "fixture transport rejected write"


def old_dump_accessors_remain_unavailable():
    model = OrderPayload.model_validate(wire_payload())
    for method in (model.dict, model.json):
        try:
            method()
            raise AssertionError("removed serializer accessor passed")
        except PydanticUserError as error:
            assert "removed" in str(error)


def protected_notes_record_the_complete_migration_boundary():
    notes = (ROOT / "contracts/pydantic_v2_migration.md").read_text()
    for phrase in (
        "`@field_validator`",
        "`@model_validator`",
        "`@field_serializer`",
        "`model_validate` and `model_validate_json`",
        "booleans are not accepted as integers",
        "model location `()`",
    ):
        assert phrase in notes


def main():
    check("current hooks publish the golden wire shape", current_hooks_validate_and_publish_the_golden_wire_shape)
    check("aliases and field names remain compatible", aliases_and_field_names_are_both_supported_without_output_drift)
    check("strict error types and locations remain stable", error_types_and_public_locations_remain_stable)
    check("field and model validator errors remain stable", field_and_model_validators_keep_their_errors_and_locations)
    check("validation and transport failures preserve causality", validation_failures_never_publish_and_are_not_swallowed)
    check("old dump accessors remain unavailable", old_dump_accessors_remain_unavailable)
    check("protected migration notes are complete", protected_notes_record_the_complete_migration_boundary)
    print(f"checks: {passed} passed, {failed} failed")
    raise SystemExit(0 if failed == 0 else 1)


if __name__ == "__main__":
    main()
