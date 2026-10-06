from querydoctor.diagnosis.rules.cardinality_misestimate import CardinalityMisestimate
from querydoctor.diagnosis.rules.high_filter_waste import HighFilterWaste
from querydoctor.diagnosis.rules.repeated_nested_loop import RepeatedNestedLoop
from querydoctor.diagnosis.rules.seq_scan_filter_waste import SeqScanFilterWaste
from querydoctor.diagnosis.rules.sort_spill import SortSpill

ALL_RULES = [
    SeqScanFilterWaste(),
    HighFilterWaste(),
    CardinalityMisestimate(),
    RepeatedNestedLoop(),
    SortSpill(),
]
