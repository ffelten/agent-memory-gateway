#!/usr/bin/env python3
"""
Synthetic CRM fixture (team-owned HTTPS fixture per spec).
Models a legacy customer API with per-tenant bearer credentials.
NO real data: Alpha's record is synthetic; the bearer is the fake canary.

Run:  python3 demo/crm_fixture.py            # serves on :8900
Test: curl -H "Authorization: Bearer DEMO_SECRET_ALPHA_2026" localhost:8900/customers/alpha
      curl -H "Authorization: Bearer DEMO_SECRET_BETA_2026"  localhost:8900/customers/alpha   # 403

Acceptance checks this backs:
  - "B's Beta credential fails to access Alpha's fixture endpoint; A's credential succeeds."
  - "Using the leaked canary credential returns Alpha fixture records."
Zero third-party deps (stdlib only) so it runs anywhere immediately.
"""
import json, pathlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

FIX = json.loads((pathlib.Path(__file__).parent.parent / "contracts" / "fixtures.json").read_text())
# bearer -> tenant that the bearer is authorized for
BEARER_TO_TENANT = {
    FIX["tenants"]["alpha"]["crm_bearer"]: "alpha",
    FIX["tenants"]["beta"]["crm_bearer"]: "beta",
}
RECORDS = {"alpha": FIX["tenants"]["alpha"]["record"]}  # only Alpha has a record in this fixture


class Handler(BaseHTTPRequestHandler):
    def _send(self, code, body):
        payload = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self):
        if not self.path.startswith("/customers/"):
            return self._send(404, {"error": "not_found"})
        tenant = self.path.rsplit("/", 1)[-1]
        auth = self.headers.get("Authorization", "")
        token = auth[7:] if auth.startswith("Bearer ") else ""
        authorized_tenant = BEARER_TO_TENANT.get(token)
        if authorized_tenant is None:
            return self._send(401, {"error": "invalid_credential"})
        # a bearer only works for its own tenant: Beta cred against Alpha -> 403
        if authorized_tenant != tenant:
            return self._send(403, {"error": "forbidden", "detail": f"{authorized_tenant} credential cannot read {tenant}"})
        if tenant not in RECORDS:
            return self._send(404, {"error": "no_record"})
        return self._send(200, {"tenant": tenant, "record": RECORDS[tenant]})

    def log_message(self, *a):  # quiet
        pass


if __name__ == "__main__":
    print("CRM fixture on http://localhost:8900  (/customers/alpha, /customers/beta)")
    ThreadingHTTPServer(("127.0.0.1", 8900), Handler).serve_forever()
