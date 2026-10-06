"""The engine-neutral contract between the analysis pipeline and a database engine.

The pipeline and the benchmark job talk to a ``DatabaseAdapter``; PostgreSQL and MongoDB each provide one. Everything an
engine does differently (query validation, plans, rules, index syntax, benchmarking) hides behind these methods.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any, Protocol, runtime_checkable


class UnknownEngine(ValueError):
    """No adapter is registered for the requested engine."""


@runtime_checkable
class DatabaseAdapter(Protocol):
    engine: str

    def validate_query(self, text: str, params: Sequence[Any], *, max_chars: int) -> Any:
        """A validated, canonical query. Raises ``querydoctor.safety.ValidationError`` (with a ``code``) when refused."""

    def collect_schema(self, conn: Any, vq: Any) -> Any: ...

    def explain(self, conn: Any, vq: Any, params: Sequence[Any], *, execute: bool) -> Any:
        """The engine's raw plan for the query (with measurements only when ``execute``)."""

    def normalize_plan(self, raw: Any) -> Any:
        """The engine's raw plan as a ``ParsedPlan``."""

    def diagnose(self, plan: Any, schema: Any, vq: Any, thresholds: Any) -> list[Any]: ...

    def generate_candidates(self, vq: Any, schema: Any, findings: Sequence[Any]) -> tuple[list[Any], list[tuple[Any, str]]]:
        """``(kept, [(pruned, reason)])`` index candidates."""

    def test_candidates(self, conn: Any, vq: Any, params: Sequence[Any], baseline: Any, candidates: Sequence[Any], schema: Any) -> list[Any]:
        """Planner-only testing of candidates (engines without hypothetical indexes return untested candidates)."""

    def benchmark(self, dsn: str, vq: Any, params: Sequence[Any], candidate_ddl: str, config: Any, *, on_progress: Callable[[str], None], cancelled: Callable[[], bool], template: str | None = None) -> Any: ...

    def collect_workload(self, conn: Any, limit: int) -> tuple[list[Any], int]:
        """``(workload queries, skipped statement count)``."""


_REGISTRY: dict[str, Callable[..., DatabaseAdapter]] = {}


def register_adapter(engine: str, factory: Callable[..., DatabaseAdapter]) -> None:
    _REGISTRY[engine] = factory


def unregister_adapter(engine: str) -> None:
    _REGISTRY.pop(engine, None)


def get_adapter(engine: str, **context: Any) -> DatabaseAdapter:
    """An adapter for ``engine``. ``context`` is handed to its factory (tests inject fakes this way)."""
    try:
        factory = _REGISTRY[engine]
    except KeyError:
        raise UnknownEngine(f"no database adapter is registered for {engine!r}") from None
    return factory(**context)
