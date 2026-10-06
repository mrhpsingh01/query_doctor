"""A local language-model explainer over Ollama's HTTP API.

The model is only ever given the evidence package as JSON and a strict system prompt; whatever it returns is a *draft*
that the claim validator checks before anyone sees it. Any failure raises ``ExplainerUnavailable`` so the caller can fall
back to the deterministic template.
"""

from __future__ import annotations

import json

import httpx

from querydoctor.explain.facts import EvidencePackage

SYSTEM_PROMPT = """You explain the results of a PostgreSQL performance analysis to a developer.
Rules:
- Use only the facts provided. Never invent a number, a table, a column, a timing or a percentage.
- Every sentence must cite the facts it relies on, written like [F3] or [F2, F5].
- Quote a number exactly as the fact states it, with its unit. You may round a number you were given.
- Never write SQL, DDL or commands: no SQL, and no CREATE, DROP or ALTER. You may name an index idea only by quoting its DDL fact verbatim.
- Say plainly what is a measurement (a benchmark), what is a prediction, and what is only a suggestion.
- Do not claim an index is safe, free or guaranteed. Be brief: at most six sentences."""

KIND_INSTRUCTIONS = {
    "plan": "Explain the execution plan to a junior developer: what the query did and where the time went.",
    "findings": "Summarise the findings: what is wrong and how serious it is.",
    "tradeoffs": "Explain the trade-offs of the index ideas: the benefit measured or predicted, and the costs.",
    "next_experiment": "Recommend the single most useful next experiment, and say why.",
    "pr_comment": "Write a short pull-request comment (plain markdown) summarising the recommendation and its evidence.",
}


class ExplainerUnavailable(RuntimeError):
    """The model could not produce a draft (the message says why and is safe to show)."""


class OllamaExplainer:
    def __init__(self, url: str, model: str | None, timeout_s: float = 60) -> None:
        self.url = url.rstrip("/")
        self.model = model or ""
        self.timeout_s = timeout_s

    @property
    def enabled(self) -> bool:
        return bool(self.model)

    def prompt(self, pkg: EvidencePackage) -> list[dict]:
        facts = [{"id": f.id, "label": f.label, "value": f.value, "unit": f.unit, "text": f.text} for f in pkg.facts]
        user = f"{KIND_INSTRUCTIONS[pkg.kind]}\n\nFacts (JSON):\n{json.dumps(facts, indent=1)}"
        return [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": user}]

    def draft(self, pkg: EvidencePackage) -> str:
        payload = {"model": self.model, "stream": False, "options": {"temperature": 0.2}, "messages": self.prompt(pkg)}
        try:
            response = httpx.post(f"{self.url}/api/chat", json=payload, timeout=self.timeout_s)
        except httpx.TimeoutException as exc:
            raise ExplainerUnavailable(f"The language model timed out after {self.timeout_s:g} seconds.") from exc
        except httpx.HTTPError as exc:
            raise ExplainerUnavailable("The language model server could not be reached.") from exc
        if response.status_code != 200:
            detail = ""
            try:
                detail = str(response.json().get("error", ""))[:200]
            except ValueError:
                pass
            raise ExplainerUnavailable(f"The language model server answered {response.status_code}{': ' + detail if detail else ''}.")
        try:
            content = response.json()["message"]["content"]
        except (ValueError, KeyError, TypeError) as exc:
            raise ExplainerUnavailable("The language model's reply was malformed.") from exc
        if not isinstance(content, str) or not content.strip():
            raise ExplainerUnavailable("The language model's reply was empty.")
        return content.strip()
