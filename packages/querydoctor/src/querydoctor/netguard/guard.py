"""SSRF protection for custom database connections.

A user-supplied host must never become a way to reach loopback services, cloud metadata endpoints or the internal
network. The host is resolved ONCE, every address it resolves to is classified, and the caller connects to the checked
IP address (``hostaddr``), so a hostile DNS server that answers differently the second time can change nothing.
"""

from __future__ import annotations

import ipaddress
import re
import socket
from collections.abc import Callable
from dataclasses import dataclass, field

DEFAULT_PORTS = frozenset({5432})
SAMPLE_HOSTS = frozenset({"target-postgres", "bench-postgres"})  # the lab's own containers, private by design
_LOCALHOST_NAMES = {"localhost", "ip6-localhost", "ip6-loopback", "localhost.localdomain"}
_HOST_CHARS = re.compile(r"^[A-Za-z0-9._:\[\]-]+$")
_LABEL = re.compile(r"^[A-Za-z0-9_]([A-Za-z0-9_-]{0,61}[A-Za-z0-9_])?$")
_CGNAT = ipaddress.ip_network("100.64.0.0/10")


class NetworkBlocked(Exception):
    """The host or port is not allowed. ``reason`` is safe to show to a person."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class GuardConfig:
    allow_private: bool = False  # RFC 1918, fc00::/7 and carrier-grade NAT; never loopback, link-local or metadata
    allowed_ports: frozenset[int] = DEFAULT_PORTS
    allowed_names: frozenset[str] = field(default_factory=lambda: SAMPLE_HOSTS)  # exempt from the address checks (not the port check)


@dataclass(frozen=True)
class ResolvedTarget:
    host: str  # the normalised host name, kept for TLS verification
    ip: str  # the address that was checked: connect to this one
    port: int


def _parse_bsd_ipv4(host: str) -> ipaddress.IPv4Address | None:
    """Decimal, hex and octal spellings (``2130706433``, ``0x7f000001``, ``0177.0.0.1``, ``127.1``) as C resolvers read them."""
    parts = host.split(".")
    if not 1 <= len(parts) <= 4 or not all(re.fullmatch(r"0[xX][0-9a-fA-F]+|[0-9]+", p) for p in parts):
        return None
    try:
        numbers = [int(p, 16) if p[:2].lower() == "0x" else int(p, 8) if len(p) > 1 and p.startswith("0") else int(p) for p in parts]
    except ValueError:
        return None
    *head, last = numbers
    if any(n > 255 for n in head) or last >= 256 ** (5 - len(parts)):
        return None
    value = 0
    for n in head:
        value = value * 256 + n
    return ipaddress.IPv4Address(value * 256 ** (5 - len(parts)) + last)


def _classify(ip: ipaddress.IPv4Address | ipaddress.IPv6Address, config: GuardConfig) -> str | None:
    """A reason this address must not be contacted, or ``None`` if it may be."""
    if isinstance(ip, ipaddress.IPv6Address):
        embedded = ip.ipv4_mapped or (ip.sixtofour if ip.sixtofour else None) or (ip.teredo[1] if ip.teredo else None)
        if ip in ipaddress.ip_network("64:ff9b::/96"):  # NAT64 carries an IPv4 address in its last 32 bits
            embedded = ipaddress.IPv4Address(int(ip) & 0xFFFFFFFF)
        if embedded is not None:
            return _classify(embedded, config) or "an IPv4 address embedded in an IPv6 address is not allowed here"
        if ip.is_site_local:
            return "site-local addresses are not allowed"
    if ip.is_loopback:
        return "loopback addresses are not allowed"
    if ip.is_unspecified:
        return "the unspecified address is not allowed"
    if ip.is_link_local:
        return "link-local addresses (including cloud metadata endpoints) are not allowed"
    if ip.is_multicast:
        return "multicast addresses are not allowed"
    if ip.is_reserved:
        return "reserved addresses are not allowed"
    if ip.is_private or (isinstance(ip, ipaddress.IPv4Address) and ip in _CGNAT):
        if config.allow_private and _is_ordinary_private(ip):
            return None
        return "private network addresses are not allowed"
    if not ip.is_global:
        return "this address range is not allowed"
    return None


def _is_ordinary_private(ip) -> bool:
    """RFC 1918, unique-local and carrier-grade NAT: the ranges an operator may opt into. Documentation and other
    special-purpose private ranges stay blocked."""
    if isinstance(ip, ipaddress.IPv4Address):
        return any(ip in ipaddress.ip_network(n) for n in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "100.64.0.0/10"))
    return ip in ipaddress.ip_network("fc00::/7")


def _normalise_host(host: str) -> str:
    if not isinstance(host, str) or not host.strip() or not host.isascii():
        raise NetworkBlocked("The host name is empty or contains unsupported characters.")
    h = host.strip().lower()
    if h.startswith("[") and h.endswith("]"):
        h = h[1:-1]
    h = h.rstrip(".")
    if not h or len(h) > 253 or not _HOST_CHARS.match(h) or "[" in h or "]" in h:
        raise NetworkBlocked("The host name is empty or contains unsupported characters.")
    return h


def _check_port(port: int, config: GuardConfig) -> None:
    if isinstance(port, bool) or not isinstance(port, int) or port not in config.allowed_ports:
        raise NetworkBlocked(f"Port {port} is not allowed. Allowed: {', '.join(str(p) for p in sorted(config.allowed_ports))}.")


def resolve_and_check(
    host: str, port: int, config: GuardConfig = GuardConfig(), *, resolver: Callable = socket.getaddrinfo  # noqa: B008
) -> ResolvedTarget:
    """Resolve ``host`` once, classify every address, and return the checked IP to connect to. Raises ``NetworkBlocked``."""
    name = _normalise_host(host)
    _check_port(port, config)

    literal: ipaddress.IPv4Address | ipaddress.IPv6Address | None = None
    if ":" in name:
        try:
            literal = ipaddress.IPv6Address(name)
        except ValueError as exc:
            raise NetworkBlocked("The host name is empty or contains unsupported characters.") from exc
    else:
        looks_numeric = bool(re.fullmatch(r"[0-9a-fx.]+", name)) and bool(re.match(r"^(0x[0-9a-f]+|[0-9]+)(\.|$)", name))
        if looks_numeric:
            literal = _parse_bsd_ipv4(name)
            if literal is None:
                raise NetworkBlocked("That is not a valid host name or IP address.")
    if literal is not None:
        reason = _classify(literal, config)
        if reason:
            raise NetworkBlocked(f"This address is blocked: {reason}.")
        return ResolvedTarget(host=name, ip=str(literal), port=port)

    if name in _LOCALHOST_NAMES or name.endswith(".localhost"):
        raise NetworkBlocked("localhost names are not allowed.")
    if not all(_LABEL.match(label) for label in name.split(".")):
        raise NetworkBlocked("The host name is empty or contains unsupported characters.")

    try:
        infos = resolver(name, port, type=socket.SOCK_STREAM)
    except (socket.gaierror, OSError, UnicodeError) as exc:
        raise NetworkBlocked("The host name could not be resolved.") from exc
    addresses: list[ipaddress.IPv4Address | ipaddress.IPv6Address] = []
    for info in infos:
        raw = str(info[4][0]).split("%")[0]
        try:
            addresses.append(ipaddress.ip_address(raw))
        except ValueError as exc:
            raise NetworkBlocked("The host name resolved to an address that could not be checked.") from exc
    if not addresses:
        raise NetworkBlocked("The host name could not be resolved.")
    if name not in config.allowed_names:
        for address in addresses:
            reason = _classify(address, config)
            if reason:
                raise NetworkBlocked(f"The host resolves to a blocked address: {reason}.")
    return ResolvedTarget(host=name, ip=str(addresses[0]), port=port)
