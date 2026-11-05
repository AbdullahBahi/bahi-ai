# -*- coding: utf-8 -*-
"""Shopify app proxy signature verification.

Shopify adds shop, path_prefix, timestamp, logged_in_customer_id and signature to every
request it forwards to this app. The signature is HMAC-SHA256 (hex) over the other query
parameters sorted by name, each written as "name=value" (repeated values joined by ","),
concatenated with no separator, keyed with the app's client secret.
"""
import hashlib, hmac, time
from functools import wraps

from flask import abort, request

import config

MAX_AGE = 15 * 60  # seconds; rejects replayed URLs while tolerating clock drift


def compute_signature(params, secret):
    """params: mapping of name -> list of values (e.g. request.args.to_dict(flat=False))."""
    message = "".join(
        f"{k}={','.join(v)}" for k, v in sorted(params.items()) if k != "signature")
    return hmac.new(secret.encode(), message.encode(), hashlib.sha256).hexdigest()


def is_valid(args, secret, now=None, check_age=True):
    sig = args.get("signature", "")
    if not sig or not secret:
        return False
    expected = compute_signature(args.to_dict(flat=False) if hasattr(args, "to_dict") else args, secret)
    if not hmac.compare_digest(expected, sig):
        return False
    if check_age:
        try:
            ts = int(args.get("timestamp", "0"))
        except (TypeError, ValueError):
            return False
        if abs((now or time.time()) - ts) > MAX_AGE:
            return False
    return True


def from_store(view):
    """Only let requests through that Shopify signed (skipped in local dev mode)."""
    @wraps(view)
    def wrapped(*a, **kw):
        if not config.DEV and not is_valid(request.args, config.PROXY_SECRET):
            abort(401)
        return view(*a, **kw)
    return wrapped
