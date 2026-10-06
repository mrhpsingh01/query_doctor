import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from test_facts import make_analysis

from querydoctor.explain import build_evidence, explain_template
from querydoctor.explain.ollama import ExplainerUnavailable, OllamaExplainer
from querydoctor.explain.service import explain


class FakeOllama:
    """A tiny stand-in for the Ollama HTTP API: programmable reply, records what it was asked."""

    def __init__(self):
        self.mode = "ok"
        self.reply = ""
        self.requests: list[dict] = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                outer.requests.append({"path": self.path, "body": body})
                if outer.mode == "slow":
                    time.sleep(1.5)
                if outer.mode == "500":
                    return self._send(500, {"error": "boom"})
                if outer.mode == "404":
                    return self._send(404, {"error": f"model '{body['model']}' not found, try pulling it first"})
                if outer.mode == "malformed":
                    raw = b"{not json"
                    self.send_response(200)
                    self.send_header("Content-Length", str(len(raw)))
                    self.end_headers()
                    self.wfile.write(raw)
                    return
                if outer.mode == "no_message":
                    return self._send(200, {"done": True})
                self._send(200, {"message": {"role": "assistant", "content": outer.reply}, "done": True})

            def _send(self, status, payload):
                raw = json.dumps(payload).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def stop(self):
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def fake():
    server = FakeOllama()
    yield server
    server.stop()


def explainer(fake, **kw):
    return OllamaExplainer(fake.url, "qd-test", timeout_s=kw.pop("timeout_s", 5), **kw)


def faithful_reply(kind="tradeoffs") -> str:
    return explain_template(build_evidence(make_analysis(), kind)).text


def test_the_prompt_carries_the_facts_as_json_and_the_rules(fake):
    fake.reply = faithful_reply()
    explain(make_analysis(), "tradeoffs", ollama=explainer(fake))
    request = fake.requests[0]
    assert request["path"] == "/api/chat" and request["body"]["stream"] is False and request["body"]["model"] == "qd-test"
    assert request["body"]["options"]["temperature"] == 0.2
    system, user = (m["content"] for m in request["body"]["messages"])
    for rule in ("only the facts", "never invent", "cite", "[F", "no SQL"):
        assert rule.lower() in system.lower(), rule
    facts = json.loads(user[user.index("["): user.rindex("]") + 1])
    assert facts[0]["id"] == "F1" and any(f["label"] == "Improvement (benchmark)" and f["value"] == 95.9 for f in facts)
    assert "tradeoffs" in user.lower() or "trade" in user.lower()


def test_a_faithful_reply_is_accepted_and_attributed_to_the_model(fake):
    fake.reply = faithful_reply()
    exp = explain(make_analysis(), "tradeoffs", ollama=explainer(fake))
    assert exp.source == "ollama:qd-test" and exp.fallback_reason is None and exp.dropped == []
    assert "95.9%" in exp.text and exp.sentences and all(s.fact_ids for s in exp.sentences)


def test_sentences_with_invented_numbers_are_dropped_and_the_rest_kept(fake):
    ok = "In the benchmark the median fell from 742 ms to 30.5 ms, which is 95.9% faster. [F8, F9, F12]"
    fake.reply = f"{ok} The index makes it 99% faster. [F12] The size is 5 MB. [F13] It also saves 4,000 ms. [F8]"
    exp = explain(make_analysis(), "tradeoffs", ollama=explainer(fake))
    assert exp.source == "ollama:qd-test" and "95.9%" in exp.text and "99%" not in exp.text
    assert len(exp.dropped) == 2 and any("99%" in d for d in exp.dropped)


def test_sentences_without_a_valid_citation_are_dropped(fake):
    fake.reply = (
        "In the benchmark the median fell from 742 ms to 30.5 ms. [F8, F9] "
        "The index is useful and small. "  # no citation
        "The index is fast. [F999] "  # a citation that does not exist
        "It was measured 7 times. [F15]"
    )
    exp = explain(make_analysis(), "tradeoffs", ollama=explainer(fake))
    assert exp.source == "ollama:qd-test" and len(exp.sentences) == 2 and len(exp.dropped) == 2


def test_a_fully_invented_reply_falls_back_to_the_template_with_the_reason(fake):
    fake.reply = "It is 12% faster. [F1] It uses 99 MB. [F2]"
    exp = explain(make_analysis(), "tradeoffs", ollama=explainer(fake))
    assert exp.source == "template" and exp.text == explain_template(build_evidence(make_analysis(), "tradeoffs")).text
    assert "no sentence" in exp.fallback_reason.lower() and exp.dropped


@pytest.mark.parametrize("reply", ["Run CREATE INDEX ON orders (id); [F1]", "Use SELECT id FROM orders WHERE x = 1 to check. [F1]", "DROP TABLE orders. [F1]"])
def test_a_reply_with_sql_is_rejected_outright(fake, reply):
    fake.reply = f"In the benchmark the median fell from 742 ms to 30.5 ms. [F8, F9] {reply}"
    exp = explain(make_analysis(), "tradeoffs", ollama=explainer(fake))
    assert exp.source == "template" and exp.fallback_reason


@pytest.mark.parametrize("mode", ["500", "404", "malformed", "no_message"])
def test_server_errors_and_malformed_replies_fall_back(fake, mode):
    fake.mode = mode
    exp = explain(make_analysis(), "plan", ollama=explainer(fake))
    assert exp.source == "template" and exp.fallback_reason
    if mode == "404":
        assert "not found" in exp.fallback_reason.lower()


def test_an_empty_reply_falls_back(fake):
    fake.reply = "   "
    exp = explain(make_analysis(), "plan", ollama=explainer(fake))
    assert exp.source == "template" and "empty" in exp.fallback_reason.lower()


def test_a_slow_server_times_out_and_falls_back(fake):
    fake.mode = "slow"
    started = time.monotonic()
    exp = explain(make_analysis(), "plan", ollama=explainer(fake, timeout_s=0.3))
    assert exp.source == "template" and "timed out" in exp.fallback_reason.lower() and time.monotonic() - started < 1.4


def closed_port_url() -> str:
    import socket

    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return f"http://127.0.0.1:{sock.getsockname()[1]}"  # nothing listens here once the socket is closed


def test_a_server_that_is_down_falls_back():
    exp = explain(make_analysis(), "plan", ollama=OllamaExplainer(closed_port_url(), "qd-test", timeout_s=3))
    assert exp.source == "template" and "reach" in exp.fallback_reason.lower()


def test_without_a_configured_model_the_template_is_used_and_nothing_touches_the_network(fake):
    for ollama in (None, OllamaExplainer(fake.url, "", timeout_s=1), OllamaExplainer(fake.url, None, timeout_s=1)):
        exp = explain(make_analysis(), "plan", ollama=ollama)
        assert exp.source == "template" and exp.fallback_reason is None
    assert fake.requests == []


def test_the_explainer_raises_a_dedicated_error_when_asked_directly():
    with pytest.raises(ExplainerUnavailable):
        OllamaExplainer(closed_port_url(), "m", timeout_s=3).draft(build_evidence(make_analysis(), "plan"))


def test_the_prompt_never_contains_query_parameters_or_connection_details(fake):
    fake.reply = faithful_reply()
    explain(make_analysis(), "pr_comment", ollama=explainer(fake))
    blob = json.dumps(fake.requests[0]["body"])
    for secret in ("alice@example.com", "s3cret", "10.1.2.3", "postgresql://"):
        assert secret not in blob
