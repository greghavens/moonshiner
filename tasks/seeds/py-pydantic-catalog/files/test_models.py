"""Contract tests for the catalog validation models — protected file."""
from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from models import Catalog, Product, Variant, summarize_errors


def product_payload(**overrides):
    payload = {
        "slug": "mug-classic",
        "name": "Classic Mug",
        "tags": ["Kitchen", "gift"],
        "variants": [
            {"sku": "MUG-1001", "price_cents": 1250, "label": "white"},
            {"sku": "MUG-1002", "price_cents": 1350, "label": "black"},
        ],
        "created_at": "2025-06-01T09:30:00Z",
    }
    payload.update(overrides)
    return payload


def the_error(exc_info):
    errors = exc_info.value.errors()
    assert len(errors) == 1, errors
    return errors[0]


# ------------------------------------------------------------------ happy path


def test_valid_product_parses():
    p = Product.model_validate(product_payload())
    assert p.slug == "mug-classic"
    assert p.variants[1].sku == "MUG-1002"
    assert p.created_at == datetime(2025, 6, 1, 9, 30, tzinfo=timezone.utc)


def test_name_is_stripped_and_tags_normalized():
    p = Product.model_validate(product_payload(
        name="  Classic Mug  ",
        tags=[" Beta", "alpha", "beta ", "ALPHA"],
    ))
    assert p.name == "Classic Mug"
    assert p.tags == ["alpha", "beta"]


def test_tags_default_to_empty_list():
    payload = product_payload()
    del payload["tags"]
    assert Product.model_validate(payload).tags == []


# ------------------------------------------------------------ field validators


def test_bad_slug_message_is_pinned():
    with pytest.raises(ValidationError) as exc:
        Product.model_validate(product_payload(slug="Mug Classic!"))
    err = the_error(exc)
    assert err["loc"] == ("slug",)
    assert err["msg"] == ("Value error, slug must contain only lowercase "
                          "letters, digits and hyphens")


def test_blank_name_rejected():
    with pytest.raises(ValidationError) as exc:
        Product.model_validate(product_payload(name="   "))
    err = the_error(exc)
    assert err["loc"] == ("name",)
    assert err["msg"] == "Value error, name must not be blank"


def test_missing_name_uses_builtin_message():
    payload = product_payload()
    del payload["name"]
    with pytest.raises(ValidationError) as exc:
        Product.model_validate(payload)
    err = the_error(exc)
    assert err["loc"] == ("name",)
    assert err["msg"] == "Field required"


def test_variant_price_must_be_positive():
    bad = product_payload()
    bad["variants"][0]["price_cents"] = 0
    with pytest.raises(ValidationError) as exc:
        Product.model_validate(bad)
    err = the_error(exc)
    assert err["loc"] == ("variants", 0, "price_cents")
    assert err["msg"] == "Input should be greater than 0"


def test_variant_sku_shape_is_validated():
    bad = product_payload()
    bad["variants"][1]["sku"] = "mug-1002"
    with pytest.raises(ValidationError) as exc:
        Product.model_validate(bad)
    err = the_error(exc)
    assert err["loc"] == ("variants", 1, "sku")
    assert err["type"] == "string_pattern_mismatch"


def test_product_needs_at_least_one_variant():
    with pytest.raises(ValidationError) as exc:
        Product.model_validate(product_payload(variants=[]))
    err = the_error(exc)
    assert err["loc"] == ("variants",)
    assert err["type"] == "too_short"


def test_unknown_fields_are_rejected():
    with pytest.raises(ValidationError) as exc:
        Product.model_validate(product_payload(colour="red"))
    err = the_error(exc)
    assert err["loc"] == ("colour",)
    assert err["msg"] == "Extra inputs are not permitted"


def test_created_at_rejects_garbage():
    with pytest.raises(ValidationError) as exc:
        Product.model_validate(product_payload(created_at="next tuesday"))
    err = the_error(exc)
    assert err["loc"] == ("created_at",)
    assert err["msg"].startswith("Input should be a valid datetime")


# ----------------------------------------------------------- model validators


def test_duplicate_variant_sku_message():
    bad = product_payload()
    bad["variants"][1]["sku"] = "MUG-1001"
    with pytest.raises(ValidationError) as exc:
        Product.model_validate(bad)
    err = the_error(exc)
    assert err["msg"] == "Value error, duplicate variant sku: MUG-1001"


def test_catalog_rejects_duplicate_slugs():
    with pytest.raises(ValidationError) as exc:
        Catalog.model_validate({"products": [product_payload(),
                                             product_payload()]})
    err = the_error(exc)
    assert err["msg"] == "Value error, duplicate product slug: mug-classic"


# -------------------------------------------------------------- serialization


def test_json_round_trip_is_lossless():
    catalog = Catalog.model_validate({"products": [product_payload()]})
    dumped = catalog.model_dump_json()
    assert Catalog.model_validate_json(dumped) == catalog
    assert '"created_at":"2025-06-01T09:30:00Z"' in dumped


def test_variant_round_trip():
    v = Variant(sku="MUG-1001", price_cents=1250, label="white")
    assert Variant.model_validate_json(v.model_dump_json()) == v


# ------------------------------------------------------------- error reporting


def test_summarize_errors_format_is_pinned():
    payload = product_payload()
    del payload["name"]
    payload["variants"][0]["price_cents"] = 0
    with pytest.raises(ValidationError) as exc:
        Catalog.model_validate({"products": [payload]})
    assert summarize_errors(exc.value) == [
        "products.0.name: Field required",
        "products.0.variants.0.price_cents: Input should be greater than 0",
    ]
