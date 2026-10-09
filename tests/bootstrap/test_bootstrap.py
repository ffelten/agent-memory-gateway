import hashlib
from io import BytesIO

import pytest

from gateway.store import Store


PUBLIC_URL = "https://docs.senso.ai/docs/knowledge-base"
TEXT = "Register a knowledge source and poll its processing status."
WHEN = "2026-10-09T12:00:00+00:00"


def test_seed_registers_permission_bound_sources_and_fixed_templates_without_tokens():
    from gateway.bootstrap import seed

    store = Store()
    seed(store, TEXT, PUBLIC_URL, WHEN)
    source = store.get("source", "src-public-runbook")
    assert source["sha256"] == hashlib.sha256(TEXT.encode()).hexdigest()
    assert source["url"] == PUBLIC_URL
    assert source["public"] is True and source["readers"] == ["*"]
    private = store.get("source", "src-alpha-private")
    assert private["readers"] == ["eng-alpha"] and private["public"] is False
    assert "100 Example Lane" in private["text"]
    assert "DEMO_SECRET" not in private["text"]
    assert store.get("template", "tpl-alpha")["source_ids"] == ["src-public-runbook", "src-alpha-private"]
    assert store.get("template", "tpl-beta-publish")["source_ids"] == ["src-public-runbook"]
    assert store.get("template", "tpl-beta-publish")["publishing"] is True
    assert store.list("token") == [] and store.list("run") == []


def test_seed_is_idempotent_without_rewriting_original_retrieval_time():
    from gateway.bootstrap import seed

    store = Store()
    seed(store, TEXT, PUBLIC_URL, WHEN)
    before = {kind: store.list(kind) for kind in ("tenant", "principal", "source", "template")}
    seed(store, TEXT, PUBLIC_URL, "2026-10-09T13:00:00+00:00")
    assert {kind: store.list(kind) for kind in before} == before


def test_changed_source_requires_operator_action_without_overwriting():
    from gateway.bootstrap import BootstrapError, seed

    store = Store()
    seed(store, TEXT, PUBLIC_URL, WHEN)
    with pytest.raises(BootstrapError) as raised:
        seed(store, "private changed content", PUBLIC_URL, WHEN)
    assert "private changed content" not in str(raised.value)
    assert store.get("source", "src-public-runbook")["text"] == TEXT


def test_rerun_never_reenables_revoked_permissions():
    from gateway.bootstrap import BootstrapError, seed

    store = Store()
    seed(store, TEXT, PUBLIC_URL, WHEN)
    source = store.get("source", "src-public-runbook")
    store.put("source", "src-public-runbook", {**source, "readers": [], "active": False})
    with pytest.raises(BootstrapError):
        seed(store, TEXT, PUBLIC_URL, WHEN)
    assert store.get("source", "src-public-runbook")["active"] is False
    assert store.get("source", "src-public-runbook")["readers"] == []


@pytest.mark.parametrize("url", ["http://docs.senso.ai/docs/knowledge-base", "https://docs.senso.ai.evil.test/docs/knowledge-base", PUBLIC_URL + "?secret=value", PUBLIC_URL + "/", "https://example.test/"])
def test_fetch_rejects_any_url_outside_exact_operator_allowlist(url):
    from gateway.bootstrap import BootstrapError, fetch_public_source

    with pytest.raises(BootstrapError) as raised:
        fetch_public_source(url)
    assert url not in str(raised.value)


def test_fetch_uses_user_agent_extracts_visible_text_and_hashes_exact_fetched_bytes():
    from gateway.bootstrap import fetch_public_source

    raw = b"<html><head><script>hidden_secret</script></head><body><h1>Runbook</h1><p>Poll until ready.</p></body></html>"

    class Response(BytesIO):
        status = 200
        headers = {"Content-Type": "text/html; charset=utf-8"}

        def geturl(self):
            return PUBLIC_URL

    class Opener:
        def open(self, request, timeout):
            assert request.get_header("User-agent")
            assert timeout == 10
            return Response(raw)

    fetched = fetch_public_source(PUBLIC_URL, opener=Opener())
    assert fetched["public_text"] == "Runbook\nPoll until ready."
    assert fetched["raw_sha256"] == hashlib.sha256(raw).hexdigest()
    assert fetched["public_url"] == PUBLIC_URL
    store = Store()
    from gateway.bootstrap import seed
    seed(store, **fetched)
    assert store.get("source", "src-public-runbook")["raw_sha256"] == fetched["raw_sha256"]


def test_fetch_rejects_oversized_body_without_echoing():
    from gateway.bootstrap import BootstrapError, fetch_public_source

    class Response(BytesIO):
        status = 200
        headers = {"Content-Type": "text/plain"}

        def geturl(self):
            return PUBLIC_URL

    class Opener:
        def open(self, request, timeout):
            return Response(b"x" * (1024 * 1024 + 1))

    with pytest.raises(BootstrapError):
        fetch_public_source(PUBLIC_URL, opener=Opener())


def test_real_urllib_redirect_handler_does_not_follow_unapproved_location(monkeypatch):
    from email.message import Message
    from urllib.request import HTTPSHandler
    from urllib.response import addinfourl
    import gateway.bootstrap as bootstrap

    calls = []

    class RedirectingHTTPSHandler(HTTPSHandler):
        def https_open(self, request):
            calls.append(request.full_url)
            headers = Message()
            headers["Location"] = "https://unapproved.example.test/private"
            response = addinfourl(BytesIO(b""), headers, request.full_url, 302)
            response.msg = "Found"
            return response

    monkeypatch.setattr(bootstrap, "HTTPSHandler", RedirectingHTTPSHandler)
    with pytest.raises(bootstrap.BootstrapError):
        bootstrap.fetch_public_source()
    assert calls == [PUBLIC_URL]


def test_cli_suppresses_raw_dependency_errors(monkeypatch, capsys):
    import gateway.bootstrap as bootstrap

    def fail(*args, **kwargs):
        raise RuntimeError("DEMO_SECRET_ALPHA_2026")

    monkeypatch.setattr(bootstrap, "fetch_public_source", fail)
    assert bootstrap.main(["--table", "gateway-test", "--profile", "default"]) == 1
    assert "DEMO_SECRET_ALPHA_2026" not in str(capsys.readouterr())
