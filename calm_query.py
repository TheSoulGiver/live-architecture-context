#!/usr/bin/env python3
"""Compact, read-only CALM callers/callees adapter for Archctx.

This attaches only to an already-managed loopback CALM Streamable HTTP server.
It intentionally does not start CALM, read its database, or claim a graph is
fresh until CALM itself reports a ready, armed watcher.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit, urlunsplit
from urllib.request import Request, urlopen

MAX_EDGES = 12
MAX_RESPONSE_BYTES = 1_000_000
PROTOCOL_VERSION = "2024-11-05"
LOOPBACK_HOSTS = {"127.0.0.1", "::1", "localhost"}


class CalmError(RuntimeError):
    """A bounded, non-sensitive CALM bridge failure."""


def endpoint(value: str) -> str:
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or parsed.hostname not in LOOPBACK_HOSTS:
        raise CalmError("CALM endpoint must be an explicit loopback http(s) URL")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise CalmError("CALM endpoint must not contain credentials, query, or fragment")
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path or "/mcp", "", ""))


def result_value(response: dict[str, Any]) -> dict[str, Any]:
    result = response.get("result")
    if not isinstance(result, dict):
        raise CalmError("CALM returned no tool result")
    if result.get("isError") is True:
        raise CalmError("CALM rejected the tool request")
    structured = result.get("structuredContent")
    if isinstance(structured, dict):
        return structured
    content = result.get("content")
    first = content[0].get("text") if isinstance(content, list) and content and isinstance(content[0], dict) else None
    if not isinstance(first, str):
        raise CalmError("CALM returned no structured tool content")
    try:
        value = json.loads(first)
    except json.JSONDecodeError as error:
        raise CalmError("CALM returned invalid structured tool content") from error
    if not isinstance(value, dict):
        raise CalmError("CALM returned non-object structured tool content")
    return value


def compact(response: dict[str, Any], direction: str) -> dict[str, Any]:
    structured = result_value(response)
    direct = structured.get("direct", [])
    if not isinstance(direct, list):
        direct = []
    edges = [{key: edge[key] for key in ("symbol", "path", "line", "edge_kind", "edge_confidence", "formal_source") if key in edge} for edge in direct[:MAX_EDGES] if isinstance(edge, dict)]
    return {"provider": "CALM", "direction": direction, "edges_ready": structured.get("edges_ready"), "direct_count": structured.get("direct_count", len(direct)), "edges": edges, "truncated": bool(structured.get("direct_truncated")) or len(direct) > MAX_EDGES, "confidence": structured.get("direct_by_confidence"), "caveat": structured.get("caveat")}


def decode_response(body: bytes, content_type: str) -> dict[str, Any]:
    text = body.decode("utf-8", errors="replace").strip()
    if not text:
        raise CalmError("CALM returned an empty response")
    if "text/event-stream" in content_type:
        events = [line[5:].strip() for line in text.splitlines() if line.startswith("data:")]
        text = events[-1] if events else ""
    try:
        response = json.loads(text)
    except json.JSONDecodeError as error:
        raise CalmError("CALM returned invalid JSON") from error
    if not isinstance(response, dict):
        raise CalmError("CALM returned a non-object response")
    if "error" in response:
        raise CalmError("CALM rejected the request")
    return response


class HttpMcp:
    def __init__(self, url: str, timeout_seconds: float):
        self.url, self.timeout_seconds, self.session_id = endpoint(url), timeout_seconds, None

    def post(self, payload: dict[str, Any], expect_response: bool = True) -> dict[str, Any] | None:
        headers = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream", "MCP-Protocol-Version": PROTOCOL_VERSION}
        if self.session_id:
            headers["Mcp-Session-Id"] = self.session_id
        request = Request(self.url, data=json.dumps(payload, separators=(",", ":")).encode(), headers=headers, method="POST")
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:
                self.session_id = response.headers.get("Mcp-Session-Id") or self.session_id
                body, content_type = response.read(MAX_RESPONSE_BYTES + 1), response.headers.get("Content-Type", "")
        except HTTPError as error:
            raise CalmError(f"CALM HTTP request failed ({error.code})") from error
        except URLError as error:
            raise CalmError("CALM endpoint is unavailable") from error
        if len(body) > MAX_RESPONSE_BYTES:
            raise CalmError("CALM response exceeded the bridge limit")
        if not expect_response:
            return None
        return decode_response(body, content_type)

    def call(self, name: str, arguments: dict[str, Any], ident: int) -> dict[str, Any]:
        response = self.post({"jsonrpc": "2.0", "id": ident, "method": "tools/call", "params": {"name": name, "arguments": arguments}})
        if response is None:
            raise CalmError("CALM did not answer the tool request")
        return response

    def initialize(self) -> None:
        response = self.post({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": PROTOCOL_VERSION, "capabilities": {}, "clientInfo": {"name": "archctx-calm-query", "version": "0.1"}}})
        if response is None or "result" not in response:
            raise CalmError("CALM did not initialize")
        self.post({"jsonrpc": "2.0", "method": "notifications/initialized"}, expect_response=False)
        result_value(self.call("repo_overview", {}, 2))


def graph_ready(response: dict[str, Any]) -> None:
    value = result_value(response)
    watcher = value.get("watcher") if isinstance(value.get("watcher"), dict) else {}
    derived = value.get("derived_status") if isinstance(value.get("derived_status"), dict) else {}
    indexed, total = value.get("files_indexed"), value.get("files_total")
    files_ready = isinstance(indexed, int) and isinstance(total, int) and indexed >= 0 and indexed == total
    expected = files_ready and value.get("indexing_phase") == "ready" and value.get("edges_ready") is True and derived.get("overall") == "ready" and watcher.get("armed") is True and watcher.get("freshness") == "fresh"
    if not expected:
        raise CalmError("CALM graph is not currently fresh")


def query_http(url: str, symbol: str, direction: str, timeout_seconds: float) -> dict[str, Any]:
    client = HttpMcp(url, timeout_seconds)
    client.initialize()
    graph_ready(client.call("indexing_status", {}, 3))
    return compact(client.call("callers" if direction == "upstream" else "callees", {"symbol": symbol, "transitive": False}, 4), direction)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", required=True)
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--direction", choices=("upstream", "downstream"), required=True)
    parser.add_argument("--endpoint", required=True, help="explicit loopback CALM Streamable HTTP endpoint")
    parser.add_argument("--timeout-seconds", type=float, default=15.0)
    args = parser.parse_args()
    if args.timeout_seconds <= 0:
        parser.error("--timeout-seconds must be positive")
    if not Path(args.repo).is_dir():
        parser.error("--repo must be an existing directory")
    try:
        print(json.dumps(query_http(args.endpoint, args.symbol, args.direction, args.timeout_seconds), ensure_ascii=False, separators=(",", ":")))
    except CalmError as error:
        print(f"archctx-calm-query: {error}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
