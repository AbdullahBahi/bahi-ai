# -*- coding: utf-8 -*-
"""The advisor's agent loop: OpenRouter (streamed) + Scento MCP tools + Tavily web search.

Shopify's app proxy buffers responses, so the browser can't receive a live stream. Each
customer message therefore runs as a background job: the browser starts it, then polls
for the text written so far. That keeps the typing-as-it-thinks feel and also avoids
proxy timeouts on long tool-using answers.
"""
import json, logging, sys, threading, time, traceback, uuid
from concurrent.futures import ThreadPoolExecutor

import requests

import config
import net
import prompts
import scento
import web_search

log = logging.getLogger("bahi-ai.agent")

JOB_TTL = 15 * 60
JOB_DEADLINE = 100  # seconds for a whole answer, tools included
JOB_REAP = JOB_DEADLINE + 20  # a job still running after this is stuck: close it and log where

ACTIVITY = {  # what the customer sees while a tool runs
    "web_search": "بدوّر على أحدث المعلومات…",
    "scento": "براجع موسوعة العطور…",
}
FALLBACK_ERROR = "معلش، حصلت مشكلة وأنا بجهّز الرد 🙏 جرّب تبعت رسالتك تاني بعد لحظات."


class Job:
    def __init__(self, client_key):
        self.id = uuid.uuid4().hex
        self.client_key = client_key
        self.status = "running"  # running | done | error
        self.text = ""
        self.activity = ""
        self.created = time.time()
        self.lock = threading.Lock()
        self.stage = "queued"  # where the worker thread is, for the logs if it gets stuck
        self.thread_id = None

    def snapshot(self):
        with self.lock:
            return {"status": self.status, "text": self.text, "activity": self.activity}


_jobs = {}
_jobs_lock = threading.Lock()


def get_job(job_id):
    with _jobs_lock:
        job = _jobs.get(job_id)
    if job:
        _reap_if_stuck(job)
    return job


def _reap_if_stuck(job):
    """Never let the customer wait for ever: a job past JOB_REAP is closed with the
    fallback message, and the stuck thread's stack goes to the logs."""
    with job.lock:
        if job.status != "running" or time.time() - job.created < JOB_REAP:
            return
        job.status = "error"
        job.activity = ""
        if not job.text.strip():
            job.text = FALLBACK_ERROR
    frame = sys._current_frames().get(job.thread_id)
    stack = "".join(traceback.format_stack(frame)) if frame else "(thread already gone)"
    log.error("job %s stuck at stage=%s after %.0fs; stack:\n%s",
              job.id[:8], job.stage, time.time() - job.created, stack)


def running_job_for(client_key):
    with _jobs_lock:
        mine = [j for j in _jobs.values() if j.client_key == client_key and j.status == "running"]
    for j in mine:
        _reap_if_stuck(j)
    return any(j.status == "running" for j in mine)


def _gc():
    cutoff = time.time() - JOB_TTL
    with _jobs_lock:
        for k in [k for k, j in _jobs.items() if j.created < cutoff]:
            del _jobs[k]


def start(history, client_key, customer_name=None):
    _gc()
    job = Job(client_key)
    with _jobs_lock:
        _jobs[job.id] = job
    threading.Thread(target=_run_safe, args=(job, history, customer_name), daemon=True).start()
    return job


# ----------------------------------------------------------------------------- LLM call

def _stream_completion(messages, tools, job, allow_tools):
    """One streamed chat completion. Appends visible text to job.text as it arrives.
    Returns (content, tool_calls, usage)."""
    payload = {
        "model": config.MODEL,
        "messages": messages,
        "stream": True,
        "max_tokens": config.MAX_OUTPUT_TOKENS,
        "temperature": config.TEMPERATURE,
        # Fast, cheap, no hidden thinking tokens: the catalogue + tools do the heavy lifting.
        "reasoning": {"enabled": False},
    }
    if tools:
        payload["tools"] = tools
        payload["tool_choice"] = "auto" if allow_tools else "none"
    headers = {
        "Authorization": f"Bearer {config.OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
        "HTTP-Referer": config.STORE_URL,
        "X-Title": "Bahi AI Advisor",
    }
    content, calls, usage = [], {}, None
    # Text written in a step that turns out to be a tool call ("let me check…") is taken
    # back off the screen; the typing indicator shows the lookup instead.
    with job.lock:
        visible_from = len(job.text)
    hidden = False
    with requests.post(config.OPENROUTER_URL, json=payload, headers=headers,
                       stream=True, timeout=(10, 30)) as r:
        if r.status_code != 200:
            raise RuntimeError(f"OpenRouter HTTP {r.status_code}: {r.text[:300]}")
        deadline = time.monotonic() + JOB_DEADLINE - (time.time() - job.created)
        for line in net.lines(r, deadline, "OpenRouter stream"):
            if not line or not line.startswith("data:"):
                continue  # keep-alive comments like ": OPENROUTER PROCESSING"
            data = line[5:].strip()
            if data == "[DONE]":
                break
            chunk = json.loads(data)
            if chunk.get("error"):
                raise RuntimeError(f"OpenRouter stream error: {chunk['error']}")
            if chunk.get("usage"):
                usage = chunk["usage"]
            for choice in chunk.get("choices", []):
                delta = choice.get("delta") or {}
                piece = delta.get("content")
                if piece:
                    content.append(piece)
                    if not hidden:
                        with job.lock:
                            job.text += piece
                            job.activity = ""
                if delta.get("tool_calls") and not hidden:
                    hidden = True
                    with job.lock:
                        job.text = job.text[:visible_from]
                for tc in delta.get("tool_calls") or []:
                    slot = calls.setdefault(tc.get("index", 0), {"id": "", "name": "", "args": ""})
                    if tc.get("id"):
                        slot["id"] = tc["id"]
                    fn = tc.get("function") or {}
                    if fn.get("name"):
                        slot["name"] += fn["name"]
                    if fn.get("arguments"):
                        slot["args"] += fn["arguments"]
    tool_calls = [calls[i] for i in sorted(calls)]
    return "".join(content), tool_calls, usage


# ----------------------------------------------------------------------------- tools

def _run_tool(call, budget):
    name = call["name"]
    try:
        args = json.loads(call["args"] or "{}")
        if not isinstance(args, dict):
            raise ValueError
    except ValueError:
        return "Invalid arguments (must be a JSON object)."
    if name == "web_search":
        with budget["lock"]:
            if budget["web"] >= config.MAX_WEB_SEARCHES:
                return "Search limit reached for this answer; answer with what you have."
            budget["web"] += 1
        return web_search.search(args.get("query", ""))
    if scento.is_scento_tool(name):
        return scento.call(name, args)
    return "Unknown tool."


def _activity_for(calls):
    if any(c["name"] == "web_search" for c in calls):
        return ACTIVITY["web_search"]
    return ACTIVITY["scento"]


# ----------------------------------------------------------------------------- loop

def _run_safe(job, history, customer_name):
    job.thread_id = threading.get_ident()
    try:
        _run(job, history, customer_name)
        with job.lock:
            if job.status != "running":
                return
            if not job.text.strip():
                job.text = FALLBACK_ERROR
            job.status = "done"
            job.activity = ""
    except Exception as e:
        log.exception("job %s failed: %s", job.id, e)
        with job.lock:
            job.status = "error"
            job.activity = ""
            if not job.text.strip():
                job.text = FALLBACK_ERROR


def _run(job, history, customer_name):
    job.stage = "loading tools"
    tools = scento.openai_tools(wait=False) + ([web_search.TOOL] if config.TAVILY_API_KEY else [])
    job.stage = "building prompt"
    messages = prompts.system_messages(customer_name) + history
    budget = {"web": 0, "lock": threading.Lock()}
    totals = {"prompt": 0, "completion": 0, "cost": 0.0, "steps": 0, "tools": []}
    try:
        for step in range(config.MAX_AGENT_STEPS):
            last = step == config.MAX_AGENT_STEPS - 1
            job.stage = f"model call {step + 1}"
            content, calls, usage = _stream_completion(messages, tools, job, allow_tools=not last)
            totals["steps"] += 1
            if usage:
                totals["prompt"] += usage.get("prompt_tokens", 0)
                totals["completion"] += usage.get("completion_tokens", 0)
                totals["cost"] += float(usage.get("cost") or 0)
            if not calls:
                return
            messages.append({
                "role": "assistant", "content": content or None,
                "tool_calls": [{"id": c["id"] or f"call_{i}", "type": "function",
                                "function": {"name": c["name"], "arguments": c["args"] or "{}"}}
                               for i, c in enumerate(calls)],
            })
            with job.lock:
                job.activity = _activity_for(calls)
            totals["tools"] += [c["name"] for c in calls]
            job.stage = "tools: " + ",".join(c["name"] for c in calls)
            with ThreadPoolExecutor(max_workers=4) as pool:
                results = list(pool.map(lambda c: _run_tool(c, budget), calls))
            for i, (c, res) in enumerate(zip(calls, results)):
                messages.append({"role": "tool", "tool_call_id": c["id"] or f"call_{i}", "content": res})
    finally:
        log.info("turn job=%s steps=%d tokens_in=%d tokens_out=%d cost=$%.5f tools=%s secs=%.1f",
                 job.id[:8], totals["steps"], totals["prompt"], totals["completion"],
                 totals["cost"], ",".join(totals["tools"]) or "-", time.time() - job.created)
