# -*- coding: utf-8 -*-
"""Bahi product knowledge (catalog.json) + live prices/stock from the public storefront feed."""
import json, logging, os, threading, time
from datetime import datetime, timezone

import requests

import config
import net

log = logging.getLogger("bahi-ai.catalog")

with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "catalog.json"), encoding="utf-8") as f:
    CATALOG = json.load(f)

PRODUCTS = CATALOG["products"]
HANDLES = [p["handle"] for p in PRODUCTS]
BY_HANDLE = {p["handle"]: p for p in PRODUCTS}

_live = {"data": None, "exp": 0.0}
_lock = threading.Lock()
LIVE_TTL = 15 * 60


def live_products():
    """{handle: {"title", "variants": [{"title","price","available"}]}} from /products.json.

    Public, unauthenticated, and exactly what shoppers see. Cached for 15 minutes; on any
    failure the last good copy (or None) is returned so the advisor never blocks on it.
    """
    with _lock:
        if _live["data"] is not None and time.time() < _live["exp"]:
            return _live["data"]
    try:
        r = net.request("GET", f"{config.STORE_URL}/products.json", total=10, params={"limit": 50})
        r.raise_for_status()
        data = {}
        for p in r.json().get("products", []):
            data[p["handle"]] = {
                "title": p["title"],
                "variants": [{"title": v["title"], "price": v["price"], "available": bool(v.get("available"))}
                             for v in p.get("variants", [])],
            }
        with _lock:
            _live["data"], _live["exp"] = data, time.time() + LIVE_TTL
        return data
    except Exception as e:  # network/JSON problems must not break the chat
        log.warning("live products fetch failed: %s", e)
        with _lock:
            _live["exp"] = time.time() + 60  # retry in a minute
            return _live["data"]


def offer_active(now=None):
    if config.OFFER_PCT <= 0:
        return False
    try:
        end = datetime.fromisoformat(config.OFFER_END)
    except ValueError:
        return False
    return (now or datetime.now(timezone.utc)) < end


def _money(x):
    v = float(x)
    return f"{v:,.0f}" if v == int(v) else f"{v:,.2f}"


def price_lines():
    """One Arabic line per product with live prices (after the offer, if active) and stock."""
    live = live_products() or {}
    pct = config.OFFER_PCT if offer_active() else 0
    lines = []
    for p in PRODUCTS:
        lp = live.get(p["handle"])
        variants = lp["variants"] if lp else [
            {"title": s["label"], "price": s["default_price"], "available": True} for s in CATALOG["sizes"]]
        parts = []
        for v in variants:
            price = float(v["price"])
            if pct:
                txt = f"{v['title']}: {_money(price * (100 - pct) / 100)} جنيه بعد الخصم (بدل {_money(price)})"
            else:
                txt = f"{v['title']}: {_money(price)} جنيه"
            if not v["available"]:
                txt += " — غير متوفر حاليًا"
            parts.append(txt)
        if lp is not None or not live:
            lines.append(f"- {p['name']} ({p['handle']}): " + "، ".join(parts))
        else:
            lines.append(f"- {p['name']} ({p['handle']}): غير معروض في المتجر حاليًا")
    return lines
