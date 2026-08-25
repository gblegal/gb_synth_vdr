"""The QA gate suite."""

from .structural import gate_01_index, gate_02_counts

ALL_GATES = [gate_01_index, gate_02_counts]

__all__ = ["ALL_GATES"]
