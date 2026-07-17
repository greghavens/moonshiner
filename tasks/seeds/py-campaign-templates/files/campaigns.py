"""Campaign objects for the email marketing service.

A campaign is a plain dict (it round-trips through the JSON API):

    {
      "name": str,
      "segments": [str, ...],          # audience segment slugs
      "settings": {
        "utm": {...},                  # tracking params stamped on links
        "send_window": {...},          # local-time hours we may send in
        "throttle_per_minute": int,
      },
    }

Campaigns are always created from BASE_TEMPLATE or duplicated from an
existing campaign; edits to one campaign must never show up on another,
and the template itself is immutable by convention.
"""

BASE_TEMPLATE = {
    "name": "untitled",
    "segments": ["all-subscribers"],
    "settings": {
        "utm": {"source": "newsletter", "medium": "email"},
        "send_window": {"start_hour": 9, "end_hour": 17},
        "throttle_per_minute": 500,
    },
}


def new_campaign(name):
    """A fresh campaign seeded from BASE_TEMPLATE."""
    campaign = dict(BASE_TEMPLATE)
    campaign["name"] = name
    return campaign


def clone_campaign(campaign, new_name):
    """Duplicate an existing campaign (the dashboard's 'Duplicate' button)."""
    duplicate = campaign.copy()
    duplicate["name"] = new_name
    return duplicate


def add_segment(campaign, segment):
    """Target one more audience segment, ignoring duplicates."""
    if segment not in campaign["segments"]:
        campaign["segments"].append(segment)


def set_utm(campaign, /, **params):
    """Set/override UTM tracking params on this campaign's links."""
    campaign["settings"]["utm"].update(params)


def set_send_window(campaign, start_hour, end_hour):
    if not (0 <= start_hour < end_hour <= 24):
        raise ValueError("send window hours out of range")
    window = campaign["settings"]["send_window"]
    window["start_hour"] = start_hour
    window["end_hour"] = end_hour
