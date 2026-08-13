"""Scheduled ESXi credential rotation against VCF 9.0 SDDC Manager.

Standard library only. See docs/contract.json for the wire contract this
package must speak, and README.md for the behaviour required of
:func:`rotate_credentials`.
"""

__all__ = ["rotate_credentials"]


def rotate_credentials(base_url, username, password, resource_type="ESXI",
                       page_size=100, poll_interval=5.0):
    """Rotate every managed credential of ``resource_type`` and return a summary.

    Returns a dict with the keys ``rotated_credential_ids`` (sorted list of
    credential ids), ``task_id``, ``task_status`` and ``token_refreshes``.
    """
    raise NotImplementedError("rotate_credentials is not implemented yet")
