"""Minimal stdlib build backend used only to keep project metadata dependency-free."""

from __future__ import annotations


def build_wheel(wheel_directory: str, config_settings=None, metadata_directory=None):
    raise RuntimeError("This exercise is run directly from its source tree")
