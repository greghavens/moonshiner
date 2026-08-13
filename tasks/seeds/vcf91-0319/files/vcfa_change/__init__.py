"""Catalog-driven change execution against the VCF Automation API in VCF 9.1.

The wire contract this package implements is transcribed in ``docs/contract.json``
from the rendered VCF Automation xAPIs reference; ``docs/official_sources.json``
records every page it was read from.
"""

from vcfa_change.change import apply_catalog_change

__all__ = ["apply_catalog_change"]
