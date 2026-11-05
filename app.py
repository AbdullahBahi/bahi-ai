# -*- coding: utf-8 -*-
"""Bahi AI perfume advisor, served inside the store at bahi-perfumes.com/apps/bahi-ai.

Routes (all reached through Shopify's app proxy, which signs every request):
  GET  /                  the page, as application/liquid so Shopify wraps it in the theme
  POST /api/chat          start an answer for the conversation sent by the browser
  GET  /api/chat/<job>    poll the answer written so far
  GET  /healthz           for UptimeRobot (no signature)
  GET  /dev               local standalone preview (only with BAHI_AI_DEV=1)
"""
import hashlib, json, logging, os, re, threading

import requests
from flask import Flask, Response, abort, jsonify, request

import agent
import catalog
import config
import limits
import scento
from proxy_auth import from_store

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("bahi-ai")

HERE = os.path.dirname(os.path.abspath(__file__))
app = Flask(__name__, static_folder="static", static_url_path="/static")
app.config["MAX_CONTENT_LENGTH"] = 64 * 1024
app.json.ensure_ascii = False


def _read(*parts):
    with open(os.path.join(HERE, *parts), encoding="utf-8") as f:
        return f.read()


SHELL = _read("templates", "shell.html")
DEV_PAGE = _read("templates", "dev.html")
ASSET_VERSION = hashlib.sha1(
    (_read("static", "bahi-ai.js") + _read("static", "bahi-ai.css")).encode()).hexdigest()[:10]

PREFIX_RE = re.compile(r"^/(apps|a|community|tools)/[A-Za-z0-9_-]{1,64}$")
SID_RE = re.compile(r"^[A-Za-z0-9_-]{8,64}$")
NAME_RE = re.compile(r"[^\w\s؀-ۿ-]", re.UNICODE)

MSG = {
    "bad": "معلش، الرسالة دي مش واضحة. جرّب تكتبها تاني.",
    "long": f"الرسالة طويلة شوية 🙏 حاول تختصرها في أقل من {config.MAX_USER_CHARS} حرف.",
    "busy_one": "لسه بجهّز ردي على رسالتك السابقة، ثانية واحدة 🙏",
    "client": "إنت بعت رسايل كتير في وقت قصير 🙏 استنى دقايق وكمّل معايا.",
    "busy": f"المستشار عليه ضغط كبير دلوقتي 🙏 جرّب بعد شوية، أو كلّمنا على واتساب {config.WHATSAPP}.",
    "off": "المستشار مش متاح دلوقتي 🙏 كلّمنا على واتساب " + config.WHATSAPP + ".",
    "gone": "الرد ده انتهت صلاحيته، ابعت رسالتك تاني.",
}


def _warm_up():
    """Load the Scento tool list and live prices in the background so the first customer
    doesn't wait for them."""
    log.info("web search: %s", "on" if config.TAVILY_API_KEY else "OFF (TAVILY_API_KEY is not set)")
    try:
        scento.openai_tools()
        catalog.live_products()
    except Exception as e:  # pragma: no cover
        log.warning("warm-up failed: %s", e)


# Started on the first request of the process that serves traffic, never at import time:
# if gunicorn loads this module and then forks its worker, a thread started here would be
# copied as a frozen half-done state (holding Python's import lock or our own locks), and
# every answer would wait on it for ever.
_warm = {"pid": None}


@app.before_request
def _ensure_warm():
    pid = os.getpid()
    if _warm["pid"] != pid:
        _warm["pid"] = pid
        threading.Thread(target=_warm_up, daemon=True).start()


# ----------------------------------------------------------------------------- page

def _prefix():
    p = request.args.get("path_prefix", "")
    return p if PREFIX_RE.match(p) else "/apps/bahi-ai"


def _asset(name, prefix):
    base = config.PUBLIC_BASE_URL or prefix
    return f"{base}/static/{name}?v={ASSET_VERSION}"


def _shell(prefix, config_js):
    return (SHELL.replace("__CSS__", _asset("bahi-ai.css", prefix))
                 .replace("__JS__", _asset("bahi-ai.js", prefix))
                 .replace("__CONFIG__", config_js))


# Product data for the cards comes from Liquid, so photos, prices, stock and variant ids
# are always the store's live values, and the offer % comes from the theme's own
# snippets/bahi-offer.liquid (the same switch the product pages use).
LIQUID_PRODUCT = (
    '{"handle":"__H__","name":__NAME__,"family":__FAMILY__'
    "{%- assign p = all_products['__H__'] -%}"
    '{%- if p.id -%},"title":{{ p.title | json }},"url":{{ p.url | json }},'
    '"image":{%- if p.featured_image -%}{{ p.featured_image | image_url: width: 400 | json }}{%- else -%}null{%- endif -%},'
    '"variants":[{%- for v in p.variants -%}{"id":{{ v.id }},"title":{{ v.title | json }},'
    '"available":{{ v.available }},"price":{{ v.price | money | json }},'
    '"sale":{{ 100 | minus: bahi_offer_pct | times: v.price | divided_by: 100 | money | json }}}'
    "{%- unless forloop.last -%},{%- endunless -%}{%- endfor -%}]{%- endif -%}}"
)
LIQUID_HEAD = (
    "{%- capture bahi_offer_pct -%}{%- render 'bahi-offer' -%}{%- endcapture -%}"
    "{%- assign bahi_offer_pct = bahi_offer_pct | strip | plus: 0 -%}\n"
)


def _liquid_config(prefix):
    prods = ",".join(
        LIQUID_PRODUCT.replace("__H__", p["handle"])
                      .replace("__NAME__", json.dumps(p["name"], ensure_ascii=False))
                      .replace("__FAMILY__", json.dumps(p["family"], ensure_ascii=False))
        for p in catalog.PRODUCTS)
    return (
        "{"
        f'"apiBase":{json.dumps(prefix)},'
        '"customerName":{{ customer.first_name | default: "" | json }},'
        '"cartUrl":{{ routes.cart_url | json }},'
        '"cartAddUrl":{{ routes.cart_add_url | json }},'
        '"offerPct":{{ bahi_offer_pct }},'
        f'"products":[{prods}]'
        "}"
    )


@app.get("/")
@from_store
def page():
    prefix = _prefix()
    body = LIQUID_HEAD + _shell(prefix, _liquid_config(prefix))
    return Response(body, content_type="application/liquid; charset=utf-8",
                    headers={"Cache-Control": "no-store"})


_DEV_CACHE = {}


@app.get("/dev")
def dev_page():
    """Standalone preview for local testing (no Shopify, no Liquid). Disabled in production."""
    if not config.DEV:
        abort(404)
    pct = config.OFFER_PCT if catalog.offer_active() else 0
    live = _DEV_CACHE.get("products")
    if not live:
        try:
            live = requests.get(f"{config.STORE_URL}/products.json", params={"limit": 50}, timeout=8).json()["products"]
            _DEV_CACHE["products"] = live
        except Exception:
            live = []
    by = {p["handle"]: p for p in live}
    prods = []
    for p in catalog.PRODUCTS:
        lp = by.get(p["handle"])
        item = {"handle": p["handle"], "name": p["name"], "family": p["family"]}
        if lp:
            item.update({
                "title": lp["title"], "url": f"{config.STORE_URL}/products/{p['handle']}",
                "image": (lp["images"][0]["src"] + "&width=400") if lp.get("images") else None,
                "variants": [{"id": v["id"], "title": v["title"], "available": v.get("available", True),
                              "price": f"{float(v['price']):,.2f} ج.م",
                              "sale": f"{float(v['price']) * (100 - pct) / 100:,.2f} ج.م"}
                             for v in lp["variants"]],
            })
        prods.append(item)
    cfg = {"apiBase": "", "customerName": request.args.get("name", ""), "cartUrl": "/cart",
           "cartAddUrl": "/cart/add", "offerPct": pct, "products": prods, "dev": True}
    html = DEV_PAGE.replace("__SHELL__", _shell("", json.dumps(cfg, ensure_ascii=False)))
    return Response(html, content_type="text/html; charset=utf-8")


# ----------------------------------------------------------------------------- chat API

def _error(key, status):
    return jsonify({"error": MSG[key]}), status


def _clean_history(raw):
    if not isinstance(raw, list) or not raw:
        return None
    msgs = []
    for m in raw[-config.MAX_HISTORY_MESSAGES:]:
        if not isinstance(m, dict) or m.get("role") not in ("user", "assistant"):
            return None
        content = m.get("content")
        if not isinstance(content, str):
            return None
        content = content.strip()
        if content:
            cap = config.MAX_USER_CHARS if m["role"] == "user" else 4000
            msgs.append({"role": m["role"], "content": content[:cap]})
    while msgs and msgs[0]["role"] != "user":
        msgs.pop(0)
    if not msgs or msgs[-1]["role"] != "user":
        return None
    return msgs


@app.post("/api/chat")
@from_store
def chat_start():
    if not config.OPENROUTER_API_KEY:
        log.error("OPENROUTER_API_KEY is not set")
        return _error("off", 503)
    data = request.get_json(silent=True) or {}
    raw = data.get("messages")
    if isinstance(raw, list) and raw and isinstance(raw[-1], dict) \
            and len(str(raw[-1].get("content", ""))) > config.MAX_USER_CHARS:
        return _error("long", 400)
    history = _clean_history(raw)
    if history is None:
        return _error("bad", 400)

    customer_id = request.args.get("logged_in_customer_id", "")
    sid = str(data.get("sid", ""))
    if customer_id.isdigit():
        client_key = "cust:" + customer_id
    elif SID_RE.match(sid):
        client_key = "sid:" + sid
    else:
        return _error("bad", 400)

    if agent.running_job_for(client_key):
        return _error("busy_one", 429)
    reason = limits.check_and_record(client_key)
    if reason:
        log.warning("rate limited (%s) %s", reason, client_key[:20])
        return _error(reason, 429)

    name = None
    if customer_id.isdigit() and isinstance(data.get("name"), str):
        name = NAME_RE.sub("", data["name"]).strip()[:30] or None
    job = agent.start(history, client_key, name)
    return jsonify({"job": job.id})


@app.get("/api/chat/<job_id>")
@from_store
def chat_poll(job_id):
    job = agent.get_job(job_id)
    if not job:
        return _error("gone", 404)
    resp = jsonify(job.snapshot())
    resp.headers["Cache-Control"] = "no-store"
    return resp


@app.get("/healthz")
def healthz():
    return "ok"


@app.after_request
def no_sniff(resp):
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    if request.path.startswith("/static/"):
        resp.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        resp.headers["Access-Control-Allow-Origin"] = "*"
    return resp


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=int(os.environ.get("PORT", 5055)), debug=False, threaded=True)
