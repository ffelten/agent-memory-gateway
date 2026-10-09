"""Trusted operator bootstrap; never creates run tokens or resets permissions."""

import argparse
from datetime import UTC, datetime
import hashlib
from html.parser import HTMLParser
import re
import ssl
import sys
from urllib.request import HTTPRedirectHandler, HTTPSHandler, Request, build_opener

from gateway.store import Conflict


PUBLIC_URL = "https://docs.senso.ai/docs/knowledge-base"
ALLOWED_PUBLIC_URLS = frozenset({PUBLIC_URL})
MAX_FETCH_BYTES = 1024 * 1024
# Leave room below DynamoDB's item limit for attributes and UTF-8 metadata.
MAX_SOURCE_BYTES = 300 * 1024


class BootstrapError(RuntimeError):
    """Sanitized operator error; never includes source or dependency content."""


class _NoRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class _VisibleText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.hidden = 0
        self.parts = []

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style", "noscript", "head"}:
            self.hidden += 1

    def handle_endtag(self, tag):
        if tag in {"script", "style", "noscript", "head"} and self.hidden:
            self.hidden -= 1

    def handle_data(self, data):
        if not self.hidden and data.strip():
            self.parts.append(data.strip())


def _validate_source(public_text, public_url, retrieved_at, raw_sha256):
    if type(public_url) is not str or public_url not in ALLOWED_PUBLIC_URLS:
        raise BootstrapError("public source URL is not allowlisted")
    if type(public_text) is not str or not public_text.strip() or len(public_text.encode("utf-8")) > MAX_SOURCE_BYTES:
        raise BootstrapError("invalid public source content")
    if type(retrieved_at) is not str:
        raise BootstrapError("invalid source retrieval time")
    try:
        timestamp = datetime.fromisoformat(retrieved_at)
        if timestamp.tzinfo is None:
            raise ValueError()
    except ValueError:
        raise BootstrapError("invalid source retrieval time") from None
    if raw_sha256 is not None and (type(raw_sha256) is not str or not re.fullmatch(r"[a-f0-9]{64}", raw_sha256)):
        raise BootstrapError("invalid source content hash")


def fetch_public_source(public_url=PUBLIC_URL, *, opener=None):
    """Fetch one exact allowlisted URL; preserve a hash of the fetched bytes.

    HTML is reduced to visible text before registration. Its registered sha256
    is computed separately by seed(), and raw_sha256 records fetched bytes.
    """
    if type(public_url) is not str or public_url not in ALLOWED_PUBLIC_URLS:
        raise BootstrapError("public source URL is not allowlisted")
    request = Request(public_url, headers={
        "User-Agent": "AgentMemoryGateway/1.0 (trusted-source-registration)",
        "Accept": "text/html, text/plain",
        "Accept-Encoding": "identity",
    })
    opener = opener or build_opener(_NoRedirects(), HTTPSHandler(context=ssl.create_default_context()))
    try:
        with opener.open(request, timeout=10) as response:
            if response.status != 200 or response.geturl() != public_url:
                raise BootstrapError("public source fetch failed")
            raw = response.read(MAX_FETCH_BYTES + 1)
            if len(raw) > MAX_FETCH_BYTES:
                raise BootstrapError("public source response too large")
            content_type = response.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
            if content_type not in {"text/html", "text/plain"}:
                raise BootstrapError("unsupported public source format")
            text = raw.decode("utf-8")
        if content_type == "text/html":
            parser = _VisibleText()
            parser.feed(text)
            parser.close()
            text = "\n".join(parser.parts)
        result = {
            "public_text": text,
            "public_url": public_url,
            "retrieved_at": datetime.now(UTC).isoformat(),
            "raw_sha256": hashlib.sha256(raw).hexdigest(),
        }
        _validate_source(**result)
        return result
    except Exception:
        raise BootstrapError("public source fetch failed") from None


def _matches(existing, proposed):
    # Preserve the original registration time on an otherwise identical rerun.
    return all(existing.get(key) == value for key, value in proposed.items() if key != "retrieved_at")


def seed(store, public_text, public_url, retrieved_at, raw_sha256=None):
    """Create fixed trusted records, refusing changes to existing policy/data."""
    _validate_source(public_text, public_url, retrieved_at, raw_sha256)
    tenant = "tenant-demo"
    private_text = "Customer name: Avery Example\nAddress: 100 Example Lane\nPurchase: Service Plan A"
    public = {
        "source_id": "src-public-runbook", "tenant_id": tenant, "text": public_text,
        "sha256": hashlib.sha256(public_text.encode("utf-8")).hexdigest(),
        "url": public_url, "retrieved_at": retrieved_at, "readers": ["*"],
        "public": True, "active": True,
    }
    if raw_sha256 is not None:
        public["raw_sha256"] = raw_sha256
    private = {
        "source_id": "src-alpha-private", "tenant_id": tenant, "text": private_text,
        "sha256": hashlib.sha256(private_text.encode("utf-8")).hexdigest(),
        "url": None, "retrieved_at": retrieved_at, "readers": ["eng-alpha"],
        "public": False, "active": True,
    }
    records = [("tenant", tenant, {"tenant_id": tenant, "active": True})]
    for principal in ("eng-alpha", "eng-beta"):
        records.append(("principal", principal, {"principal_id": principal, "tenant_id": tenant, "active": True}))
    records.extend([("source", public["source_id"], public), ("source", private["source_id"], private)])
    for template_id, principal_id, source_ids, publishing in (
        ("tpl-alpha", "eng-alpha", ["src-public-runbook", "src-alpha-private"], False),
        ("tpl-beta", "eng-beta", ["src-public-runbook"], False),
        ("tpl-beta-publish", "eng-beta", ["src-public-runbook"], True),
    ):
        records.append(("template", template_id, {
            "template_id": template_id, "tenant_id": tenant, "principal_id": principal_id,
            "source_ids": source_ids, "publishing": publishing, "active": True,
        }))
    try:
        # Find known conflicts before creating anything. Writes are exclusive;
        # a concurrent writer can never be overwritten after this preflight.
        current = {(kind, key): store.get(kind, key) for kind, key, _ in records}
        for kind, key, proposed in records:
            existing = current[kind, key]
            if existing is not None and not _matches(existing, proposed):
                raise BootstrapError("bootstrap conflict; operator action required")
        result = {"created": 0, "unchanged": 0}
        for kind, key, proposed in records:
            if current[kind, key] is not None:
                result["unchanged"] += 1
                continue
            try:
                store.put(kind, key, proposed, create_only=True)
                result["created"] += 1
            except Conflict:
                existing = store.get(kind, key)
                if existing is None or not _matches(existing, proposed):
                    raise BootstrapError("bootstrap conflict; operator action required") from None
                result["unchanged"] += 1
        return result
    except BootstrapError:
        raise
    except Exception:
        raise BootstrapError("bootstrap storage unavailable") from None


def main(argv=None):
    parser = argparse.ArgumentParser(description="Register trusted gateway demo sources and templates")
    parser.add_argument("--table", required=True)
    parser.add_argument("--region", default="us-east-1")
    parser.add_argument("--profile", default="default")
    parser.add_argument("--public-url", default=PUBLIC_URL)
    arguments = parser.parse_args(argv)
    try:
        import boto3
        from botocore.config import Config
        from gateway.dynamo import DynamoStore

        fetched = fetch_public_source(arguments.public_url)
        session = boto3.Session(profile_name=arguments.profile, region_name=arguments.region)
        config = Config(connect_timeout=3, read_timeout=5, retries={"max_attempts": 1})
        table = session.resource("dynamodb", config=config).Table(arguments.table)
        result = seed(DynamoStore(table), **fetched)
        print(f"Bootstrap complete: {result['created']} created, {result['unchanged']} unchanged.")
        return 0
    except BootstrapError as error:
        print(str(error), file=sys.stderr)
        return 1
    except Exception:
        print("Bootstrap unavailable.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
