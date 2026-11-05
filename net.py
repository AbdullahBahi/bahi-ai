# -*- coding: utf-8 -*-
"""HTTP calls with a hard wall-clock limit.

requests' `timeout` only bounds the connect and the gap between bytes, and requests'
iter_content/iter_lines wait until a whole block (512+ bytes) has arrived. A server that
keeps a connection open and sends little bits (keep-alive pings, a slow proxy) can
therefore block a call for ever. Every outbound call goes through here, reading whatever
has arrived (`read1`) and checking the clock between reads, so nothing can hang a
customer's answer.
"""
import time

import requests

STEP = 10.0  # connect / between-bytes timeout


def chunks(r, deadline, what="request"):
    """Yield the (decoded) body of a stream=True response as it arrives; raise
    requests.Timeout once time.monotonic() passes `deadline`."""
    raw = r.raw
    while True:
        if time.monotonic() > deadline:
            raise requests.Timeout(f"{what}: deadline passed")
        if hasattr(raw, "read1"):  # urllib3 >= 2.2: returns as soon as some data is there
            b = raw.read1(65536, decode_content=True)
        else:
            b = raw.read(1, decode_content=True)
        if not b:
            return
        yield b


def lines(r, deadline, what="stream"):
    """Decoded text lines of a stream=True response (for SSE), with the same deadline."""
    buf = b""
    for b in chunks(r, deadline, what):
        buf += b
        while b"\n" in buf:
            line, buf = buf.split(b"\n", 1)
            yield line.rstrip(b"\r").decode("utf-8", "replace")
    if buf:
        yield buf.decode("utf-8", "replace")


def request(method, url, total, **kw):
    """Like requests.request, but the whole call (connect + headers + body) must finish
    within `total` seconds, otherwise requests.Timeout is raised."""
    deadline = time.monotonic() + total
    step = min(STEP, total)
    with requests.request(method, url, stream=True, timeout=(step, step), **kw) as r:
        body = b"".join(chunks(r, deadline, f"{method} {url}"))
        r._content = body  # lets r.json() / r.text work after the stream is closed
        r._content_consumed = True
    return r
