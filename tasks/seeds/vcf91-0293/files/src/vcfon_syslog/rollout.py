"""Apply a syslog forwarding plan to one appliance and report what landed.

Standard library only.
"""

from __future__ import annotations

from .client import SyslogSettingsClient


def apply_syslog_plan(plan, base_url, timeout=10.0):
    """Apply ``plan`` to the appliance at ``base_url`` and return a report dict.

    See README.md for the required run order and the exact report shape.
    """
    raise NotImplementedError
