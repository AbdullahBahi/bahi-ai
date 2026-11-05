# -*- coding: utf-8 -*-
"""Minimal client for the Scento Perfume API MCP server (remote, read-only, no auth).

Speaks MCP's Streamable HTTP transport directly (JSON-RPC over POST) so the app needs no
MCP SDK. The tool list is fetched from the server and exposed to the LLM as OpenAI-style
function tools named "scento_<tool>"; calls are forwarded with tools/call.
"""
import itertools, json, logging, re, threading, time

import requests

import config

log = logging.getLogger("bahi-ai.scento")

PROTOCOL_VERSION = "2025-06-18"
# Everything except journal-search (blog articles: not useful here, and links customers away).
ALLOWED = {
    "perfume-search", "perfume-portrait", "scent-neighbours", "perfume-contrast",
    "note-portrait", "material-match", "house-portrait", "family-map",
    "occasion-edit", "wardrobe-composer", "perfumery-lexicon",
}
# Arguments the model shouldn't set: answers come back in English regardless, and paging
# isn't worth the extra round trips in a chat.
HIDDEN_ARGS = {"lang", "continuation"}
MAX_RESULT_CHARS = 3500
TOOLS_TTL = 6 * 3600

_ids = itertools.count(1)
_state = {"session": None, "tools": None, "exp": 0.0}
_lock = threading.Lock()


def _parse(resp):
    """JSON-RPC result from either a JSON body or an SSE stream body."""
    ctype = resp.headers.get("Content-Type", "")
    if "text/event-stream" in ctype:
        for line in resp.text.splitlines():
            if line.startswith("data:"):
                msg = json.loads(line[5:].strip())
                if "result" in msg or "error" in msg:
                    return msg
        raise RuntimeError("empty SSE response")
    return resp.json()


def _rpc(method, params=None, notify=False, timeout=20):
    headers = {"Content-Type": "application/json",
               "Accept": "application/json, text/event-stream",
               "MCP-Protocol-Version": PROTOCOL_VERSION}
    if _state["session"]:
        headers["Mcp-Session-Id"] = _state["session"]
    body = {"jsonrpc": "2.0", "method": method}
    if params is not None:
        body["params"] = params
    if not notify:
        body["id"] = next(_ids)
    r = requests.post(config.SCENTO_MCP_URL, json=body, headers=headers, timeout=timeout)
    if r.headers.get("Mcp-Session-Id"):
        _state["session"] = r.headers["Mcp-Session-Id"]
    if notify:
        return None
    r.raise_for_status()
    msg = _parse(r)
    if "error" in msg:
        raise RuntimeError(f"MCP error: {msg['error']}")
    return msg["result"]


def _initialize():
    _state["session"] = None
    _rpc("initialize", {"protocolVersion": PROTOCOL_VERSION, "capabilities": {},
                        "clientInfo": {"name": "bahi-ai", "version": "1.0"}})
    try:
        _rpc("notifications/initialized", notify=True)
    except requests.RequestException:
        pass


def _to_openai(tool):
    schema = json.loads(json.dumps(tool.get("inputSchema") or {"type": "object", "properties": {}}))
    props = schema.get("properties", {})
    for k in HIDDEN_ARGS:
        props.pop(k, None)
    if "required" in schema:
        schema["required"] = [r for r in schema["required"] if r in props]
        if not schema["required"]:
            schema.pop("required")
    return {"type": "function", "function": {
        "name": "scento_" + tool["name"].replace("-", "_"),
        "description": "[Scento perfume database] " + (tool.get("description") or tool.get("title", "")),
        "parameters": schema,
    }}


def openai_tools():
    """Function-tool definitions for the LLM; [] if Scento is unreachable (retried later)."""
    with _lock:
        if _state["tools"] is not None and time.time() < _state["exp"]:
            return _state["tools"]
        try:
            _initialize()
            tools = _rpc("tools/list", {})["tools"]
            _state["tools"] = [_to_openai(t) for t in tools if t["name"] in ALLOWED]
            _state["exp"] = time.time() + TOOLS_TTL
            log.info("scento tools loaded: %d", len(_state["tools"]))
        except Exception as e:
            log.warning("scento tools/list failed: %s", e)
            if _state["tools"] is None:
                _state["tools"] = []
            _state["exp"] = time.time() + 300
        return _state["tools"]


def is_scento_tool(name):
    return name.startswith("scento_")


def _clean(text):
    # Drop the "Further reading" link list: it costs tokens and the advisor shouldn't send
    # customers to other sites anyway.
    text = re.sub(r"\n+Further reading:\n(?:- .*\n?)+", "\n", text)
    return text.strip()[:MAX_RESULT_CHARS]


def call(name, args):
    """Run a scento_* tool. Returns plain text for the LLM (errors included, never raises)."""
    mcp_name = name[len("scento_"):].replace("_", "-")
    if mcp_name not in ALLOWED:
        return "Unknown tool."
    args = {k: v for k, v in (args or {}).items() if k not in HIDDEN_ARGS}
    for attempt in range(2):
        try:
            res = _rpc("tools/call", {"name": mcp_name, "arguments": args}, timeout=25)
            text = "\n".join(c.get("text", "") for c in res.get("content", []) if c.get("type") == "text")
            if res.get("isError"):
                return "Lookup failed: " + text[:500]
            return _clean(text) or "No result."
        except Exception as e:
            log.warning("scento %s failed (attempt %d): %s", mcp_name, attempt + 1, e)
            if attempt == 0:  # a dropped/expired session: start a fresh one once
                with _lock:
                    try:
                        _initialize()
                    except Exception:
                        pass
    return "The perfume database is unavailable right now; answer from your own knowledge."
