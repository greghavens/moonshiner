"""Onboard a batch of vCenter data sources behind their prechecks.

Standard library only.
"""

from __future__ import annotations

from .client import VcenterDataSourceClient


def onboard_vcenters(plan, base_url, timeout=10.0):
    """Run ``plan`` against the appliance at ``base_url`` and return a report dict.

    See README.md for the required run order, the precheck gate and the exact
    report shape.
    """
    raise NotImplementedError
