"""Trusted-harness helper: mint the three per-run tokens from the real gateway.

Reads the ADMIN bearer from env GATEWAY_ADMIN_TOKEN or the ignored operator
file .gateway-admin.json ({"admin_token": ...}) and calls POST /v1/admin/runs
on GATEWAY_BASE_URL. Writes GATEWAY_TOKEN_ALPHA / _BETA / _BETA_PUB to the
ignored file .gateway-runs.env (chmod 600). Never prints any token. The admin
bearer must never be placed in an agent process environment.

Usage:  GATEWAY_BASE_URL=https://... uv run python3 demo/mint_runs.py
        then:  set -a; . ./.gateway-runs.env; set +a   (agents get only their own token)
"""
import json
import os
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "agents"))
from gateway_client import GatewayClient  # noqa: E402

RUNS = (  # env var, template, principal
    ("GATEWAY_TOKEN_ALPHA", "tpl-alpha", "eng-alpha"),
    ("GATEWAY_TOKEN_BETA", "tpl-beta", "eng-beta"),
    ("GATEWAY_TOKEN_BETA_PUB", "tpl-beta-publish", "eng-beta"),
)
OUT_FILE = pathlib.Path(os.environ.get("GATEWAY_RUNS_ENV_FILE") or ROOT / ".gateway-runs.env")


def read_admin_token(root=ROOT):
    tok = os.environ.get("GATEWAY_ADMIN_TOKEN")
    if tok:
        return tok
    f = pathlib.Path(root) / ".gateway-admin.json"
    if f.exists():
        return json.loads(f.read_text())["admin_token"]
    raise SystemExit("no admin bearer: set GATEWAY_ADMIN_TOKEN or create .gateway-admin.json")


def mint(base_url, admin_token, runs=RUNS):
    """Return {ENV_NAME: token}. Raises RuntimeError (no secrets in message) on failure."""
    admin = GatewayClient(base_url, admin_token)
    out = {}
    for env_name, template, principal in runs:
        r = admin._request("POST", "/v1/admin/runs", {"template_id": template, "principal_id": principal})
        if "token" not in r:
            raise RuntimeError(f"mint failed for {template}: HTTP {r.get('_status')} {r.get('error')}")
        out[env_name] = r["token"]
    return out


def write_env_file(tokens, path=OUT_FILE):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write("".join(f"{k}={v}\n" for k, v in tokens.items()))
    os.chmod(path, 0o600)


def main():
    base = os.environ.get("GATEWAY_BASE_URL")
    if not base:
        raise SystemExit("GATEWAY_BASE_URL is required")
    try:
        tokens = mint(base, read_admin_token())
    except RuntimeError as e:
        raise SystemExit(str(e))
    write_env_file(tokens)
    print(f"minted {len(tokens)} runs ({', '.join(tokens)}); wrote {OUT_FILE.name} (mode 600); tokens not printed")


if __name__ == "__main__":
    main()
