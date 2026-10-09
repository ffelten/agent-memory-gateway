"""Small HTTPS ClickHouse sink with no credential-bearing redirects.

Protocol: https://clickhouse.com/docs/interfaces/http
"""

import base64
import json
import math
import re
import ssl
from urllib.parse import urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, HTTPSHandler, Request, build_opener

from analytics.events import COLUMNS, export_event


MAX_RESPONSE_BYTES = 4096
MAX_REQUEST_BYTES = 1024 * 1024


class SinkError(RuntimeError):
    """Bounded failure safe to expose without a provider body or credential."""


class _RejectRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class ClickHouseSink:
    """Synchronous JSONEachRow inserts; the optional opener is a test boundary."""

    def __init__(self, url, username, password, database="default", *, timeout=5, opener=None):
        try:
            parsed = urlsplit(url)
            port = parsed.port
            valid_url = (
                type(url) is str
                and not any(character.isspace() for character in url)
                and parsed.scheme == "https"
                and bool(parsed.hostname)
                and parsed.username is None
                and parsed.password is None
                and parsed.path in ("", "/")
                and not parsed.query
                and not parsed.fragment
                and (port is None or 1 <= port <= 65535)
            )
        except (TypeError, ValueError):
            valid_url = False
        if not valid_url:
            raise ValueError("invalid audit sink configuration")
        if type(database) is not str or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,63}", database):
            raise ValueError("invalid audit sink configuration")
        if any(type(value) is not str or not value or len(value) > 4096 for value in (username, password)):
            raise ValueError("invalid audit sink configuration")
        if ":" in username or any(ord(char) < 32 for char in username + password):
            raise ValueError("invalid audit sink configuration")
        if type(timeout) not in (int, float) or not math.isfinite(timeout) or not 0 < timeout <= 10:
            raise ValueError("invalid audit sink configuration")
        self._url = url.rstrip("/") + "/"
        self._database = database
        self._authorization = "Basic " + base64.b64encode(f"{username}:{password}".encode()).decode("ascii")
        self._timeout = timeout
        self._opener = opener or build_opener(_RejectRedirects(), HTTPSHandler(context=ssl.create_default_context()))

    def insert(self, rows):
        """Return only after an empty successful synchronous insert response."""
        if not rows:
            return
        if len(rows) > 1000:
            raise ValueError("invalid audit batch limit")
        events = [export_event(row) for row in rows]
        data = ("\n".join(json.dumps(event, separators=(",", ":"), allow_nan=False) for event in events) + "\n").encode("utf-8")
        if len(data) > MAX_REQUEST_BYTES:
            raise ValueError("audit batch too large")
        query = f"INSERT INTO gateway_events ({', '.join(COLUMNS)}) FORMAT JSONEachRow"
        params = urlencode({
            "database": self._database,
            "query": query,
            "async_insert": "0",
            "wait_end_of_query": "1",
            "date_time_input_format": "best_effort",
        })
        request = Request(
            self._url + "?" + params,
            data=data,
            headers={"Authorization": self._authorization, "Content-Type": "application/x-ndjson"},
            method="POST",
        )
        try:
            with self._opener.open(request, timeout=self._timeout) as response:
                body = response.read(MAX_RESPONSE_BYTES + 1)
                if (
                    response.status != 200
                    or response.headers.get("X-ClickHouse-Exception-Code", "0") != "0"
                    or len(body) > MAX_RESPONSE_BYTES
                    or body.strip()
                ):
                    raise SinkError("audit sink unavailable")
        except Exception:
            # HTTPError/URLError can include URLs, response bodies, or secrets.
            raise SinkError("audit sink unavailable") from None
