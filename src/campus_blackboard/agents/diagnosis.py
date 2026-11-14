"""Diagnosis-agent handler factory."""

from .runner import build_specialist_handler


def build_handler(settings=None, **dependencies):
    return build_specialist_handler("diagnosis", settings, **dependencies)
