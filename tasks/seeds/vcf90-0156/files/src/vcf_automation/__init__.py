"""Small stdlib-only client for the reference-derived VCF Automation contract."""

from .change import DeploymentChangeResult, apply_deployment_change
from .client import VcfAutomationClient, VcfAutomationError

__all__ = [
    "DeploymentChangeResult",
    "VcfAutomationClient",
    "VcfAutomationError",
    "apply_deployment_change",
]
