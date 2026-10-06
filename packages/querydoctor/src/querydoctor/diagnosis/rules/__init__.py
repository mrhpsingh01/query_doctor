from querydoctor.diagnosis.rules.cardinality_misestimate import CardinalityMisestimate
from querydoctor.diagnosis.rules.duplicate_subtree import DuplicateSubtree
from querydoctor.diagnosis.rules.function_on_column import FunctionOnColumn
from querydoctor.diagnosis.rules.hash_spill import HashSpill
from querydoctor.diagnosis.rules.high_filter_waste import HighFilterWaste
from querydoctor.diagnosis.rules.index_filter_remainder import IndexFilterRemainder
from querydoctor.diagnosis.rules.repeated_nested_loop import RepeatedNestedLoop
from querydoctor.diagnosis.rules.seq_scan_filter_waste import SeqScanFilterWaste
from querydoctor.diagnosis.rules.sort_spill import SortSpill
from querydoctor.diagnosis.rules.wide_result import WideResult

ALL_RULES = [
    SeqScanFilterWaste(),
    HighFilterWaste(),
    CardinalityMisestimate(),
    RepeatedNestedLoop(),
    SortSpill(),
    HashSpill(),
    IndexFilterRemainder(),
    FunctionOnColumn(),
    WideResult(),
    DuplicateSubtree(),
]

# When the key rule fires on a node, the listed (less specific) rules are dropped for that same node.
SUPERSEDES: dict[str, set[str]] = {"PG_INDEX_FILTER_REMAINDER": {"PG_HIGH_FILTER_WASTE"}}
