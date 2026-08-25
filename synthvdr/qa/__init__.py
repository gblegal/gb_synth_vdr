"""The QA gate suite."""

from .leakage import (
    gate_03_flag_leakage,
    gate_04_vocabulary,
    gate_05_index_vocabulary,
    gate_12_key_containment,
    gate_14_unchecked_names,
)
from .structural import gate_01_index, gate_02_counts

ALL_GATES = [
    gate_01_index,
    gate_02_counts,
    gate_03_flag_leakage,
    gate_04_vocabulary,
    gate_05_index_vocabulary,
    gate_12_key_containment,
    gate_14_unchecked_names,
]

__all__ = ["ALL_GATES"]
