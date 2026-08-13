"""Loopback mock of the VCF Automation deployment APIs, pinned to docs/contract.json.

This package is test scaffolding. It binds only to 127.0.0.1 and never contacts a
VMware endpoint. Do not modify it; the verifier reads its request log.
"""

from .server import MockVcfAutomation, load_contract, load_fixtures

__all__ = ["MockVcfAutomation", "load_contract", "load_fixtures"]
