from contextlib import contextmanager
from http.client import HTTPConnection
import json
from threading import Thread

from agents.gateway_client import GatewayClient
from gateway.service import Gateway, digest
from gateway.store import Store


@contextmanager
def serving(gateway):
    from gateway.local import make_server

    server = make_server(gateway)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_existing_gateway_client_reads_seeded_public_source_and_cannot_read_alpha(capsys):
    from gateway.bootstrap import seed

    store = Store()
    seed(store, "Poll until ready.", "https://docs.senso.ai/docs/knowledge-base", "2026-10-09T12:00:00+00:00")
    gateway = Gateway(store, object(), admin_token_hash=digest("admin-test-token"), report_base_url="http://127.0.0.1")
    with serving(gateway) as server:
        base_url = f"http://127.0.0.1:{server.server_port}"
        admin = GatewayClient(base_url, "admin-test-token")
        run = admin._request("POST", "/v1/admin/runs", {"template_id": "tpl-beta", "principal_id": "eng-beta"})
        client = GatewayClient(base_url, run["token"])
        assert client.get_source("src-public-runbook")["text"] == "Poll until ready."
        assert client.get_source("src-alpha-private")["_status"] == 403
    output = str(capsys.readouterr())
    assert "admin-test-token" not in output and run["token"] not in output
    assert "/v1/" not in output


def test_large_and_ambiguous_body_is_rejected_before_gateway_handler():
    class App:
        def handle(self, *args):
            raise AssertionError("oversized request reached gateway")

    with serving(App()) as server:
        connection = HTTPConnection("127.0.0.1", server.server_port, timeout=3)
        connection.request("POST", "/v1/memories", headers={"Content-Length": "32769"})
        response = connection.getresponse()
        assert response.status == 413
        assert json.loads(response.read()) == {"error": "body_too_large"}
        connection.close()
        connection = HTTPConnection("127.0.0.1", server.server_port, timeout=3)
        connection.putrequest("POST", "/v1/memories")
        connection.putheader("Content-Length", "0")
        connection.putheader("Content-Length", "1")
        connection.endheaders()
        response = connection.getresponse()
        assert response.status == 400
        response.read()
        connection.close()


def test_handler_exception_and_unknown_methods_never_echo_private_path_or_error(capsys):
    class App:
        def handle(self, *args):
            raise RuntimeError("DEMO_SECRET_ALPHA_2026")

    with serving(App()) as server:
        connection = HTTPConnection("127.0.0.1", server.server_port, timeout=3)
        connection.request("GET", "/DEMO_SECRET_ALPHA_2026")
        response = connection.getresponse()
        assert response.status == 503
        assert b"DEMO_SECRET_ALPHA_2026" not in response.read()
        connection.close()
    assert "DEMO_SECRET_ALPHA_2026" not in str(capsys.readouterr())
