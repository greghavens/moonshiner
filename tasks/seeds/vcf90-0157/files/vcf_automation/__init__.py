"""Small stdlib-only client for the protected VCF Automation contract."""

from .client import PrecheckFailed, ProjectClient, VCFAutomationError

__all__ = ["PrecheckFailed", "ProjectClient", "VCFAutomationError"]
