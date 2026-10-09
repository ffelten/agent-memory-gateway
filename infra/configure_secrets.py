"""Update existing sponsor secrets from ignored local configuration.

Run from the repository root:
    python -m infra.configure_secrets --env-file .env \
        --deployment-file build/deployment.json --profile default --region us-east-1

Values are literal: no shell execution, variable expansion, or escape decoding.
Exit codes: 0 all configured; 2 incomplete/invalid inputs; 1 dependency failure.
Only call this trusted operator command when the local credentials are ready.
"""

import argparse
import json
from pathlib import Path
import re
import sys
from urllib.parse import urlsplit


MAX_FILE_BYTES = 64 * 1024


class ConfigurationError(RuntimeError):
    """Bounded configuration failure without file contents or secret values."""


def _read_file(path):
    try:
        with Path(path).open("rb") as handle:
            raw = handle.read(MAX_FILE_BYTES + 1)
        if len(raw) > MAX_FILE_BYTES:
            raise ValueError()
        return raw.decode("utf-8-sig")
    except Exception:
        raise ConfigurationError("configuration file unavailable or invalid") from None


def read_env(path):
    """Parse KEY=value lines, optional export/quotes/comments, with no evaluation."""
    values = {}
    for line in _read_file(path).splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        key, separator, value = line.partition("=")
        comment_only = bool(re.match(r"\s+#", value))
        key, value = key.strip(), value.strip()
        if not separator or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key) or key in values:
            raise ConfigurationError("invalid environment file")
        if value.startswith(("'", '"')):
            quoted = re.fullmatch(r"(['\"])(.*?)\1(?:\s+#.*)?", value)
            if quoted is None:
                raise ConfigurationError("invalid environment file")
            value = quoted.group(2)
        else:
            value = "" if comment_only else re.split(r"\s+#", value, maxsplit=1)[0].rstrip()
        if any(ord(character) < 32 for character in value):
            raise ConfigurationError("invalid environment file")
        values[key] = value
    return values


def _present(value):
    return type(value) is str and bool(value.strip())


def _host_url(host, https_port=None):
    parsed = urlsplit("https://" + host)
    if not parsed.hostname or parsed.path or parsed.query or parsed.fragment or parsed.username is not None:
        raise ValueError()
    if https_port:
        if not re.fullmatch(r"[0-9]{1,5}", https_port) or not 1 <= int(https_port) <= 65535:
            raise ValueError()
        if parsed.port is not None and parsed.port != int(https_port):
            raise ValueError()
    port = int(https_port) if https_port else 8443
    return "https://" + parsed.netloc + (f":{port}" if parsed.port is None else "")


def _prepare(values):
    """Return private update payloads separately from safe operator statuses."""
    updates, result = {}, {}
    missing = [name for name in ("SENSO_API_KEY", "SENSO_FOLDER_ID") if not _present(values.get(name))]
    if missing:
        result["senso"] = {"status": "missing", "missing": missing}
    elif any(len(values[name]) > 4096 or any(ord(character) < 32 for character in values[name]) for name in ("SENSO_API_KEY", "SENSO_FOLDER_ID")):
        result["senso"] = {"status": "invalid", "fields": ["SENSO_API_KEY", "SENSO_FOLDER_ID"]}
    else:
        updates["senso"] = {"api_key": values["SENSO_API_KEY"], "folder_id": values["SENSO_FOLDER_ID"]}

    endpoint = values.get("CLICKHOUSE_URL") or values.get("CLICKHOUSE_HOST")
    username = values.get("CLICKHOUSE_USERNAME") or values.get("CLICKHOUSE_USER")
    password = values.get("CLICKHOUSE_PASSWORD")
    missing = []
    for name, value in (
        ("CLICKHOUSE_URL/CLICKHOUSE_HOST", endpoint),
        ("CLICKHOUSE_USERNAME/CLICKHOUSE_USER", username),
        ("CLICKHOUSE_PASSWORD", password),
    ):
        if not _present(value):
            missing.append(name)
    if missing:
        result["clickhouse"] = {"status": "missing", "missing": missing}
    else:
        try:
            from analytics.clickhouse import ClickHouseSink

            config = {
                "url": values.get("CLICKHOUSE_URL") or _host_url(values["CLICKHOUSE_HOST"], values.get("CLICKHOUSE_HTTPS_PORT")),
                "username": username,
                "password": password,
                "database": values.get("CLICKHOUSE_DATABASE") or "default",
            }
            # Reuse the runtime's HTTPS, credential, and database checks. The
            # constructor does not contact ClickHouse or send any credential.
            ClickHouseSink(**config)
            updates["clickhouse"] = config
        except Exception:
            result["clickhouse"] = {"status": "invalid", "fields": ["CLICKHOUSE_URL/CLICKHOUSE_HOST", "CLICKHOUSE_HTTPS_PORT", "CLICKHOUSE_USERNAME/CLICKHOUSE_USER", "CLICKHOUSE_PASSWORD", "CLICKHOUSE_DATABASE"]}
    return updates, result


def configure_integrations(values, deployment, *, client=None, client_factory=None):
    """Apply each complete integration independently; return names/status only.

    An injected SDK client or lazy factory supports isolated tests. Incomplete
    inputs do not construct a client and do not retrieve existing secrets.
    """
    updates, result = _prepare(values)
    keys = {"senso": "gateway_config_secret_arn", "clickhouse": "clickhouse_secret_arn"}
    for integration, update in updates.items():
        target = deployment.get(keys[integration])
        if not _present(target):
            result[integration] = {"status": "missing", "missing": [keys[integration]]}
            continue
        try:
            if client is None:
                if client_factory is None:
                    raise ConfigurationError("secret client unavailable")
                client = client_factory()
            if integration == "senso":
                existing = json.loads(client.get_secret_value(SecretId=target)["SecretString"])
                if not isinstance(existing, dict) or not isinstance(existing.get("senso", {}), dict):
                    raise ConfigurationError("existing gateway configuration invalid")
                # Preserve admin hash, report URL, all unrelated top-level
                # configuration, and optional existing Senso fields.
                payload = {**existing, "senso": {**existing.get("senso", {}), **update}}
            else:
                payload = update
            client.put_secret_value(SecretId=target, SecretString=json.dumps(payload, separators=(",", ":")))
            result[integration] = {"status": "updated"}
        except Exception:
            # SDK errors can include request data. Never stringify or log them.
            result[integration] = {"status": "error", "reason": "dependency_unavailable"}
    return result


def main(argv=None, *, client=None, client_factory=None):
    parser = argparse.ArgumentParser(description="Load complete sponsor settings into existing managed secrets")
    parser.add_argument("--env-file", default=".env")
    parser.add_argument("--deployment-file", default="build/deployment.json")
    parser.add_argument("--profile", default="default")
    parser.add_argument("--region", default="us-east-1")
    arguments = parser.parse_args(argv)
    try:
        values = read_env(arguments.env_file)
        deployment = json.loads(_read_file(arguments.deployment_file))
        if not isinstance(deployment, dict):
            raise ConfigurationError("invalid deployment configuration")

        def make_client():
            import boto3
            from botocore.config import Config

            session = boto3.Session(profile_name=arguments.profile, region_name=arguments.region)
            return session.client("secretsmanager", config=Config(connect_timeout=3, read_timeout=5, retries={"max_attempts": 1}))

        result = configure_integrations(values, deployment, client=client, client_factory=client_factory or make_client)
        for name in ("senso", "clickhouse"):
            status = result[name]
            if status["status"] == "updated":
                print(f"{name}: updated")
            elif status["status"] in {"missing", "invalid"}:
                fields = status.get("missing", status.get("fields", []))
                print(f"{name}: {status['status']}: {', '.join(fields)}")
            else:
                print(f"{name}: update unavailable")
        if any(item["status"] == "error" for item in result.values()):
            return 1
        return 0 if all(item["status"] == "updated" for item in result.values()) else 2
    except Exception:
        print("Configuration unavailable; verify the local files and AWS profile.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
