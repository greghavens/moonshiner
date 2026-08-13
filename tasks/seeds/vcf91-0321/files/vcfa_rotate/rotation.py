"""The rotation itself.

Standard library only.
"""

from __future__ import annotations


def rotate_named_credential(
    client,
    credential_name,
    vc_urn,
    new_username,
    new_password,
    probe_host,
    probe_port,
    max_polls=30,
):
    """Rotate the secret behind a named credential without stranding in-flight work.

    Args:
        client: a VcfaClient.
        credential_name: ``name`` of the named credential currently in use.
        vc_urn: URN of the vCenter registration that consumes it.
        new_username: username for the replacement credential.
        new_password: the new secret.
        probe_host: host to probe before touching anything.
        probe_port: port to probe.
        max_polls: give up after this many polls of any single wait loop.

    Returns:
        dict with keys:
            old_credential_id  -- id of the credential that was retired
            new_credential_id  -- id of the credential that replaced it
            probe              -- the ConnectionProbeResult from the pre-flight probe
            repoint_task_uri   -- task URI from repointing the vCenter
            retire_task_uri    -- task URI from retiring the old credential
            drain_polls        -- how many audit-trail polls the drain wait took

    Raises:
        TimeoutError: if a wait loop exceeds ``max_polls``.
    """
    raise NotImplementedError
