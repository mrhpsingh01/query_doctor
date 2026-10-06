from test_connections_registry import make_client, mapping, resolver_for  # noqa: F401  (fixtures)


def test_the_privacy_page_states_exactly_what_is_stored_and_what_is_not(make_client):  # noqa: F811
    with make_client() as client:
        body = client.get("/api/privacy").json()
    stored = " | ".join(body["stores"]).lower()
    for item in ("query", "parameters", "execution plan", "schema", "findings", "benchmark", "prediction", "explanation", "feedback", "encrypted"):
        assert item in stored, item
    never = " | ".join(body["never_stores"]).lower()
    assert "rows" in never and "plain" in never
    assert any("DELETE /api/analyses" in d for d in body["deletion"])
    assert any("DELETE /api/connections" in d for d in body["deletion"])
    assert body["custom_connections_enabled"] is True


def test_the_page_reflects_the_custom_connection_flag(make_client):  # noqa: F811
    with make_client(enabled=False) as client:
        assert client.get("/api/privacy").json()["custom_connections_enabled"] is False


def test_the_privacy_page_never_contains_secrets(make_client):  # noqa: F811
    with make_client() as client:
        text = client.get("/api/privacy").text.lower()
    assert "postgresql://" not in text and "password=" not in text
