"""Where QueryDoctor may connect. In this release only the prepared sample databases are reachable, which also
closes off SSRF through user-supplied connection details."""

ALLOWED_HOSTS = frozenset({"target-postgres", "bench-postgres", "localhost", "127.0.0.1"})
ALLOWED_PORTS = frozenset({5432, 5433, 5434})


def connection_allowed(host: str, port: int) -> bool:
    return host.lower() in ALLOWED_HOSTS and port in ALLOWED_PORTS
