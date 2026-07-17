"""Behavior checks for the intake form schema builder.

Run: python3 test_form_schema.py
"""
from form_schema import FormSchema


def test_explicit_validators_enforced():
    schema = FormSchema("harborview")
    schema.add_field("full_name", validators=[("required",), ("max_len", 80)])
    schema.add_field("contact_pref", kind="choice",
                     validators=[("one_of", ["email", "phone", "mail"])])
    schema.add_field("zip", validators=[("digits",), ("max_len", 5)])

    ok = schema.check_submission(
        {"full_name": "Ada Osei", "contact_pref": "email", "zip": "60614"})
    assert ok == {}, f"clean submission should have no errors, got {ok}"

    bad = schema.check_submission(
        {"full_name": "", "contact_pref": "fax", "zip": "6O614"})
    assert "full_name" in bad, "blank mandatory field should be reported"
    assert "contact_pref" in bad, "unlisted choice should be reported"
    assert "zip" in bad, "non-digit zip should be reported"


def test_optional_fields_stay_optional():
    schema = FormSchema("lakeview")
    schema.add_field("insurance_id")
    schema.add_field("notes")
    schema.add_field("referral_source")
    schema.require("insurance_id")

    result = schema.check_submission({"insurance_id": "BCX-4471", "notes": ""})
    assert result == {}, (
        "only insurance_id was marked mandatory; blank notes/referral_source "
        f"must be accepted, got {result}"
    )
    assert schema.rules_for("notes") == [], schema.rules_for("notes")
    assert schema.rules_for("referral_source") == [], schema.rules_for("referral_source")


def test_each_clinic_gets_its_own_form():
    lakeview = FormSchema("lakeview")
    lakeview.add_field("insurance_id", validators=[("required",)])
    lakeview.add_field("notes")

    riverside = FormSchema("riverside")
    assert riverside.field_names() == [], (
        f"a brand-new schema must start empty, got {riverside.field_names()}"
    )

    riverside.add_field("phone", validators=[("digits",)])
    assert "phone" not in lakeview.field_names(), (
        "configuring riverside must not change the lakeview form"
    )
    assert riverside.field_names() == ["phone"], riverside.field_names()


def test_rules_added_later_target_one_field():
    address_rules = [("max_len", 40)]
    schema = FormSchema("northgate")
    schema.add_field("street", validators=address_rules)
    schema.add_field("city", validators=address_rules)
    schema.add_rule("street", "required")

    assert schema.rules_for("city") == [("max_len", 40)], (
        f"city picked up rules meant for street: {schema.rules_for('city')}"
    )
    result = schema.check_submission({"street": "12 Pine Ct", "city": ""})
    assert result == {}, f"blank city should still be accepted, got {result}"


def main():
    test_explicit_validators_enforced()
    test_optional_fields_stay_optional()
    test_each_clinic_gets_its_own_form()
    test_rules_added_later_target_one_field()
    print("all checks passed")


if __name__ == "__main__":
    main()
