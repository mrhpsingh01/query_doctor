import socket

import pytest

from querydoctor.netguard import GuardConfig, NetworkBlocked, ResolvedTarget, resolve_and_check

PUBLIC = "93.184.216.34"


def resolver_for(mapping: dict[str, list[str]]):
    """A resolver that answers from a table and records how often it was asked."""
    calls: list[str] = []

    def resolve(host, port, *args, **kwargs):
        calls.append(host)
        if host not in mapping:
            raise socket.gaierror(socket.EAI_NONAME, "Name or service not known")
        out = []
        for ip in mapping[host]:
            family = socket.AF_INET6 if ":" in ip else socket.AF_INET
            sockaddr = (ip, port, 0, 0) if family == socket.AF_INET6 else (ip, port)
            out.append((family, socket.SOCK_STREAM, 6, "", sockaddr))
        return out

    resolve.calls = calls
    return resolve


def check(host, port=5432, config=None, mapping=None):
    return resolve_and_check(host, port, config or GuardConfig(), resolver=resolver_for(mapping or {}))


@pytest.mark.parametrize(
    "host",
    [
        "127.0.0.1", "127.1.2.3", "::1", "0.0.0.0", "::", "169.254.169.254", "169.254.0.1", "fe80::1", "fd00:ec2::254",
        "10.1.2.3", "10.255.255.255", "172.16.0.1", "172.31.255.255", "192.168.1.1", "100.64.0.1", "100.127.255.255", "fc00::1",
        "fd12:3456::1", "::ffff:127.0.0.1", "::ffff:10.0.0.1", "::ffff:169.254.169.254", "224.0.0.1", "239.255.255.250", "ff02::1",
        "240.0.0.1", "255.255.255.255", "192.0.2.1", "198.51.100.7", "203.0.113.9", "fec0::1", "[::1]", "2002:7f00:1::1", "64:ff9b::7f00:1",
    ],
)
def test_internal_and_special_addresses_are_blocked_whatever_they_are_written_as(host):
    with pytest.raises(NetworkBlocked):
        check(host)


@pytest.mark.parametrize(
    "host", ["2130706433", "0x7f000001", "0177.0.0.1", "127.1", "0x7f.1", "017700000001", "0300.0250.0.1", "2852039166", "0xA9FEA9FE"]
)
def test_alternative_spellings_of_internal_addresses_are_blocked(host):
    with pytest.raises(NetworkBlocked):
        check(host)


@pytest.mark.parametrize("host", ["localhost", "LOCALHOST", "localhost.", "app.localhost", "ip6-localhost", "ip6-loopback", "localhost.localdomain"])
def test_localhost_style_names_are_blocked_without_even_resolving(host):
    resolver = resolver_for({host.lower().rstrip("."): [PUBLIC]})  # even if a resolver claims it is public
    with pytest.raises(NetworkBlocked):
        resolve_and_check(host, 5432, GuardConfig(), resolver=resolver)
    assert resolver.calls == []


@pytest.mark.parametrize(
    "host", ["", " ", "db.example.com@127.0.0.1", "a b", "db/../x", "db.example.com:5432", "exämple.com", "１２７.０.０.１", "db\nhost", "-bad.example.com", "a" * 300 + ".com"]
)
def test_malformed_or_tricky_host_strings_are_blocked(host):
    with pytest.raises(NetworkBlocked):
        check(host, mapping={host: [PUBLIC]})


@pytest.mark.parametrize("port", [22, 23, 25, 80, 443, 3306, 6379, 27017, 9200, 0, 65536, -1, 5433])
def test_only_allowlisted_ports_are_allowed(port):
    with pytest.raises(NetworkBlocked) as exc:
        check(PUBLIC, port=port)
    assert "port" in exc.value.reason.lower()


def test_a_name_resolving_to_several_addresses_is_blocked_if_any_is_internal():
    with pytest.raises(NetworkBlocked):
        check("db.example.com", mapping={"db.example.com": [PUBLIC, "10.0.0.7"]})
    with pytest.raises(NetworkBlocked):
        check("db.example.com", mapping={"db.example.com": ["2606:4700::1", "::ffff:127.0.0.1"]})


def test_a_name_that_does_not_resolve_is_blocked_with_a_clear_reason():
    with pytest.raises(NetworkBlocked) as exc:
        check("nope.example.com")
    assert "resolve" in exc.value.reason.lower()


def test_an_empty_answer_is_blocked():
    with pytest.raises(NetworkBlocked):
        resolve_and_check("db.example.com", 5432, GuardConfig(), resolver=lambda *a, **k: [])


def test_a_public_address_and_a_public_name_are_allowed():
    assert check(PUBLIC) == ResolvedTarget(host=PUBLIC, ip=PUBLIC, port=5432)
    target = check("db.example.com", mapping={"db.example.com": [PUBLIC]})
    assert (target.host, target.ip, target.port) == ("db.example.com", PUBLIC, 5432)
    assert check("2606:4700:4700::1111").ip == "2606:4700:4700::1111"
    assert check("db.example.com.", mapping={"db.example.com": [PUBLIC]}).host == "db.example.com"  # a trailing dot is normalised


def test_private_ranges_are_allowed_only_when_the_operator_opts_in():
    cfg = GuardConfig(allow_private=True)
    for host in ("10.1.2.3", "172.16.0.1", "192.168.1.1", "100.64.0.1", "fc00::1"):
        assert check(host, config=cfg).ip == host
    assert check("db.corp", config=cfg, mapping={"db.corp": ["10.9.9.9"]}).ip == "10.9.9.9"


@pytest.mark.parametrize("host", ["127.0.0.1", "::1", "169.254.169.254", "0.0.0.0", "224.0.0.1", "::ffff:127.0.0.1", "2130706433", "fe80::1", "localhost"])
def test_opting_into_private_networks_never_unblocks_loopback_link_local_or_metadata(host):
    with pytest.raises(NetworkBlocked):
        check(host, config=GuardConfig(allow_private=True))


def test_the_sample_hosts_are_allowlisted_by_name_even_though_they_resolve_to_private_addresses():
    for name in ("target-postgres", "bench-postgres"):
        assert check(name, mapping={name: ["172.20.0.5"]}).ip == "172.20.0.5"
    with pytest.raises(NetworkBlocked):  # an allowlisted name is not a licence for other ports
        check("target-postgres", port=22, mapping={"target-postgres": ["172.20.0.5"]})
    with pytest.raises(NetworkBlocked):  # nor for a different name
        check("target-postgres-2", mapping={"target-postgres-2": ["172.20.0.5"]})


def test_custom_allowed_ports_and_names_are_honoured():
    cfg = GuardConfig(allowed_ports=frozenset({5432, 6543}), allowed_names=frozenset({"pg.internal"}))
    assert check(PUBLIC, port=6543, config=cfg).port == 6543
    assert check("pg.internal", config=cfg, mapping={"pg.internal": ["10.0.0.4"]}).ip == "10.0.0.4"


def test_dns_rebinding_is_closed_the_checked_address_is_the_one_returned_and_dns_is_asked_once():
    answers = [[PUBLIC], ["127.0.0.1"]]  # a hostile server answers differently the second time

    def resolver(host, port, *a, **k):
        ip = answers.pop(0)[0] if answers else "127.0.0.1"
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, port))]

    target = resolve_and_check("rebind.example.com", 5432, GuardConfig(), resolver=resolver)
    assert target.ip == PUBLIC and len(answers) == 1  # exactly one lookup; the connection must use target.ip


def test_ipv6_scope_ids_in_resolver_answers_are_ignored_for_classification():
    resolver = resolver_for({"db.example.com": ["fe80::1"]})
    with pytest.raises(NetworkBlocked):
        resolve_and_check("db.example.com", 5432, GuardConfig(), resolver=resolver)


def test_the_block_reason_never_echoes_credentials_or_userinfo():
    with pytest.raises(NetworkBlocked) as exc:
        check("user:s3cret@10.0.0.1")
    assert "s3cret" not in exc.value.reason
