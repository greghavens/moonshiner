"""VCF migration architecture generator."""

__all__ = ["build_architecture"]


def build_architecture(*args, **kwargs):
    """Load the implementation lazily so the package remains importable."""
    from .planner import build_architecture as implementation

    return implementation(*args, **kwargs)

