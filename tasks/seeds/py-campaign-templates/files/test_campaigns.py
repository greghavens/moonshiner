"""Behavior checks for campaign creation/cloning. Run: python3 test_campaigns.py"""
from campaigns import (
    BASE_TEMPLATE,
    add_segment,
    clone_campaign,
    new_campaign,
    set_send_window,
    set_utm,
)


def main():
    spring = new_campaign("spring-sale")
    add_segment(spring, "vip-customers")
    set_utm(spring, campaign="spring-sale")
    set_send_window(spring, 8, 12)

    # A campaign created afterwards starts from a pristine template.
    summer = new_campaign("summer-sale")
    assert summer["segments"] == ["all-subscribers"], (
        f"new campaign inherited another campaign's segments: {summer['segments']!r}")
    assert summer["settings"]["utm"] == {"source": "newsletter", "medium": "email"}, (
        f"new campaign inherited UTM edits: {summer['settings']['utm']!r}")
    assert summer["settings"]["send_window"] == {"start_hour": 9, "end_hour": 17}, (
        f"new campaign inherited a send window: {summer['settings']['send_window']!r}")

    # Duplicating a campaign gives an independent copy.
    variant = clone_campaign(spring, "spring-sale-b")
    add_segment(variant, "lapsed-customers")
    set_utm(variant, source="promo")
    set_send_window(variant, 14, 18)

    assert variant["name"] == "spring-sale-b"
    assert "lapsed-customers" in variant["segments"]
    assert "lapsed-customers" not in spring["segments"], (
        f"editing the duplicate changed the original's segments: {spring['segments']!r}")
    assert spring["settings"]["utm"]["source"] == "newsletter", (
        f"editing the duplicate changed the original's UTM: {spring['settings']['utm']!r}")
    assert spring["settings"]["send_window"] == {"start_hour": 8, "end_hour": 12}, (
        f"editing the duplicate moved the original's send window: "
        f"{spring['settings']['send_window']!r}")

    # The original still has everything that was set on it.
    assert spring["segments"] == ["all-subscribers", "vip-customers"]
    assert spring["settings"]["utm"]["campaign"] == "spring-sale"

    # And the shared template itself never changed.
    assert BASE_TEMPLATE["segments"] == ["all-subscribers"], (
        f"template segments were mutated: {BASE_TEMPLATE['segments']!r}")
    assert BASE_TEMPLATE["settings"]["utm"] == {"source": "newsletter", "medium": "email"}, (
        f"template UTM was mutated: {BASE_TEMPLATE['settings']['utm']!r}")
    assert BASE_TEMPLATE["settings"]["send_window"] == {"start_hour": 9, "end_hour": 17}, (
        f"template send window was mutated: {BASE_TEMPLATE['settings']['send_window']!r}")

    print("all checks passed")


if __name__ == "__main__":
    main()
