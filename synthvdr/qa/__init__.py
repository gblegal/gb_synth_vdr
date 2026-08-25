"""The QA gate suite."""

from .depth import gate_10_depth
from .integrity import gate_11_subset, gate_13_fact_sheet, gate_15_discoverability
from .leakage import (
    gate_03_flag_leakage,
    gate_04_vocabulary,
    gate_05_index_vocabulary,
    gate_12_key_containment,
    gate_14_unchecked_names,
)
from .renders import gate_16_render_parity
from .structural import (
    gate_01_index,
    gate_02_counts,
    gate_06_dir_canon,
    gate_07_twin_diff,
    gate_08_carrier_census,
    gate_09_xrefs,
)

ALL_GATES = [
    gate_01_index,
    gate_02_counts,
    gate_03_flag_leakage,
    gate_04_vocabulary,
    gate_05_index_vocabulary,
    gate_06_dir_canon,
    gate_07_twin_diff,
    gate_08_carrier_census,
    gate_09_xrefs,
    gate_10_depth,
    gate_11_subset,
    gate_12_key_containment,
    gate_13_fact_sheet,
    gate_14_unchecked_names,
    gate_15_discoverability,
    gate_16_render_parity,
]

__all__ = ["ALL_GATES"]
