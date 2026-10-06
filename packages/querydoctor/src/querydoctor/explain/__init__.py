from querydoctor.explain.facts import KINDS, EvidencePackage, Fact, build_evidence
from querydoctor.explain.ollama import ExplainerUnavailable, OllamaExplainer
from querydoctor.explain.service import explain
from querydoctor.explain.template import Explanation, Sentence, explain_template
from querydoctor.explain.validator import ValidationResult, validate_claims

__all__ = [
    "KINDS", "EvidencePackage", "Explanation", "ExplainerUnavailable", "Fact", "OllamaExplainer", "Sentence", "ValidationResult", "build_evidence", "explain", "explain_template",
    "validate_claims",
]
