# -*- coding: utf-8 -*-
"""Search-engine side of the advisor page (bahi-perfumes.com/apps/bahi-ai).

The chat itself is empty until someone types, so on its own the page gives Google (and AI
search engines) almost nothing to index. This module adds, on the full page only (never in
the floating window):

  * a guide under the chat, rendered on the server: what Bahi helps with (each item starts
    that question in the chat), the "inspired by" list linking every Bahi perfume to the
    famous perfume it is inspired by, and a FAQ;
  * JSON-LD: WebPage + WebApplication + BreadcrumbList + FAQPage + ItemList.

TITLE and DESCRIPTION are also used by ../apply_ai_seo.py, which puts them into the theme's
<title>, meta description and Open Graph tags (Shopify gives app-proxy pages none of their
own). Change them here, then run that script again.
"""
import html
import json

import catalog
import config

PATH = "/apps/bahi-ai"
URL = config.STORE_URL + PATH

# ~60 characters before the brand, so Google shows it whole; the English brand name last
# (it is also what people search for).
TITLE = "باهي، مستشار العطور بالذكاء الاصطناعي: اعرف عطرك المناسب | Bahi Perfumes"
DESCRIPTION = ("اسأل باهي، مستشار العطور الذكي من Bahi Perfumes، مجانًا وبالعربي: يرشّحلك العطر "
               "المناسب لذوقك وللشغل والسهرات والهدايا، ويشرحلك النوتات والثبات والفوحان.")

# What Bahi helps with. Each card's button sends `ask` to the chat.
HELP = [
    ("تلاقي العطر المناسب لذوقك",
     "قولّه بتحب إيه وبتكره إيه في الروايح، وهو يسألك كام سؤال بسيط ويرشّحلك العطر اللي يشبهك.",
     "ساعدني ألاقي عطري المناسب"),
    ("عطر للشغل أو للسهرة أو لكل يوم",
     "عطر هادي ومحترم للمكتب، ولا عطر قوي وفخم للسهرات والمناسبات؟ باهي يقولك تحط إيه وإمتى.",
     "عايز عطر مناسب للشغل"),
    ("هدية عطر مضمونة",
     "مش عارف ذوق الشخص؟ احكي لباهي عنه شوية، وهو يختارلك هدية تفرّحه رجالي أو حريمي.",
     "بدوّر على هدية لحد غالي عليّا"),
    ("عطور الصيف وعطور الشتا",
     "إيه العطر اللي يستحمل حر الصيف في مصر، وإيه العطر الدافي اللي يليق بالشتا والليل؟",
     "إيه أنسب عطر للصيف في مصر؟"),
    ("تفهم النوتات والعائلات العطرية",
     "يعني إيه شرقي خشبي ولا شيبري فاكهي؟ وإيه الفرق بين نوتات المقدمة والقلب والقاعدة؟",
     "إيه هي العائلات العطرية الرئيسية؟"),
    ("الثبات والفوحان والمقارنة بين العطور",
     "اسأله عن أي عطر في العالم: مكوناته وثباته وفوحانه، أو خلّيه يقارنلك بين عطرين.",
     "إيه الفرق بين الثبات والفوحان؟"),
]

INSPIRED_ASK = "عطري المفضل مش في القايمة، رشّحلي الأقرب ليه من باهي"

FAQ = [
    ("إيه هو باهي؟",
     "باهي مستشار عطور بالذكاء الاصطناعي من Bahi Perfumes. بتكلّمه بالعربي زي ما بتكلّم حد فاهم في "
     "العطور، وهو يساعدك تعرف ذوقك وتختار عطرك، ويضيفلك العطر للسلة من نفس المحادثة."),
    ("هل استخدام باهي مجاني؟",
     "أيوه، مجاني تمامًا ومن غير تسجيل. اسأل براحتك قبل ما تقرر تشتري."),
    ("إزاي باهي بيعرف العطر المناسب ليا؟",
     "بيسألك عن الروايح اللي بتحبها، والمناسبة والموسم، والعطور اللي جرّبتها قبل كده، وبعدين يطابق "
     "ذوقك مع العائلات العطرية ونوتات عطور باهي ويرشّحلك الأقرب ليك."),
    ("هل باهي بيعرف عطور تانية غير عطور باهي؟",
     "أيوه. يقدر يكلّمك عن أي عطر عالمي: نوتاته وعائلته العطرية وثباته وفوحانه، ويقارن بين العطور. "
     "ولما ييجي للترشيح، بيقولك أنسب عطر ليك من تشكيلة باهي."),
    ("إيه عطر باهي المستوحى من عطري المفضل؟",
     "كل عطور باهي مستوحاة من عطور عالمية مشهورة، زي وهج المستوحى من Baccarat Rouge 540 وظفر المستوحى "
     "من Creed Aventus. القايمة الكاملة فوق، ولو عطرك مش فيها اسأل باهي وهو يرشّحلك الأقرب لذوقك."),
    ("أقدر أطلب العطر من المحادثة؟",
     "أيوه. مع كل ترشيح بيظهرلك كارت العطر بالسعر والأحجام المتاحة، وتقدر تضيفه للسلة على طول. "
     "والتوصيل لكل محافظات مصر."),
    ("لو عايز أكلّم حد من فريق باهي؟",
     f"كلّمنا على واتساب {config.WHATSAPP} وهنرد عليك."),
]


def _e(s):
    return html.escape(s, quote=True)


def _ask_button(text, label="اسأل باهي"):
    return (f'<button type="button" class="bahi-ai__ask" data-bahi-ask="{_e(text)}">'
            f'{_e(label)}<span aria-hidden="true"> ←</span></button>')


def _inspired():
    """[(Bahi product, original name, its Arabic name)] for products that name an inspiration."""
    rows = []
    for p in catalog.PRODUCTS:
        src = (p.get("inspired_by") or "").strip()
        if not src:
            continue
        en, _, ar = (s.strip() for s in src.partition("—"))
        rows.append((p, en, ar))
    return rows


def guide_html():
    help_items = "".join(
        f'<li class="bahi-ai__help-item"><h3>{_e(t)}</h3><p>{_e(d)}</p>{_ask_button(a)}</li>'
        for t, d, a in HELP)

    rows = "".join(
        "<li class=\"bahi-ai__match\">"
        f'<span class="bahi-ai__match-src"><span lang="en" dir="ltr">{_e(en)}</span>'
        + (f'<small>{_e(ar)}</small>' if ar else "") + "</span>"
        '<span class="bahi-ai__match-arrow" aria-hidden="true">←</span>'
        f'<span class="bahi-ai__match-bahi"><a href="/products/{_e(p["handle"])}">{_e(p["name"])}</a>'
        f'<small>{_e(p["family"])} · {_e(p.get("gender", ""))}</small></span>'
        "</li>"
        for p, en, ar in _inspired())

    faq = "".join(
        f'<details class="bahi-ai__faq-item"><summary><h3>{_e(q)}</h3></summary><p>{_e(a)}</p></details>'
        for q, a in FAQ)

    return (
        '<div class="bahi-ai__guide">'
        '<section class="bahi-ai__block" aria-labelledby="bahi-ai-help">'
        '<h2 id="bahi-ai-help" class="bahi-ai__h2">باهي يقدر يساعدك في إيه؟</h2>'
        '<p class="bahi-ai__lead">مستشار عطور شخصي بيتكلم عربي، فاهم في النوتات والعائلات العطرية، '
        'وعارف كل عطر في تشكيلة باهي. اختار سؤال وابدأ، أو اكتب سؤالك بطريقتك.</p>'
        f'<ul class="bahi-ai__help">{help_items}</ul>'
        "</section>"

        '<section class="bahi-ai__block" aria-labelledby="bahi-ai-inspired">'
        '<h2 id="bahi-ai-inspired" class="bahi-ai__h2">بتحب عطر عالمي؟ اعرف عطر باهي المستوحى منه</h2>'
        '<p class="bahi-ai__lead">كل عطور باهي مستوحاة من أشهر الروائح العالمية. لو عطرك المفضل هنا، ده '
        'أقرب عطر ليه عندنا، ولو مش هنا، باهي يرشّحلك الأقرب لذوقك.</p>'
        f'<ul class="bahi-ai__matches">{rows}</ul>'
        f'<p class="bahi-ai__more">{_ask_button(INSPIRED_ASK, "عطري مش في القايمة")}</p>'
        "</section>"

        '<section class="bahi-ai__block" aria-labelledby="bahi-ai-faq">'
        '<h2 id="bahi-ai-faq" class="bahi-ai__h2">أسئلة شائعة عن باهي</h2>'
        f'<div class="bahi-ai__faq">{faq}</div>'
        "</section>"
        "</div>"
    )


def json_ld():
    org = {"@type": "Organization", "@id": config.STORE_URL + "/#organization",
           "name": "Bahi Perfumes", "alternateName": "باهي", "url": config.STORE_URL}
    graph = [
        {"@type": "WebPage", "@id": URL + "#webpage", "url": URL, "name": TITLE,
         "description": DESCRIPTION, "inLanguage": "ar-EG",
         "isPartOf": {"@type": "WebSite", "@id": config.STORE_URL + "/#website",
                      "url": config.STORE_URL, "name": "Bahi Perfumes"},
         "breadcrumb": {"@id": URL + "#breadcrumb"}, "mainEntity": {"@id": URL + "#app"}},
        {"@type": "WebApplication", "@id": URL + "#app", "url": URL,
         "name": "باهي — مستشار العطور بالذكاء الاصطناعي",
         "alternateName": ["Bahi AI", "مستشار باهي", "اسأل باهي"],
         "description": DESCRIPTION, "inLanguage": "ar",
         "applicationCategory": "LifestyleApplication", "operatingSystem": "Any",
         "browserRequirements": "Requires JavaScript", "isAccessibleForFree": True,
         "offers": {"@type": "Offer", "price": "0", "priceCurrency": "EGP"},
         "featureList": [t for t, _, _ in HELP], "provider": org},
        {"@type": "BreadcrumbList", "@id": URL + "#breadcrumb", "itemListElement": [
            {"@type": "ListItem", "position": 1, "name": "الرئيسية", "item": config.STORE_URL + "/"},
            {"@type": "ListItem", "position": 2, "name": "اسأل باهي", "item": URL}]},
        {"@type": "FAQPage", "@id": URL + "#faq", "mainEntity": [
            {"@type": "Question", "name": q, "acceptedAnswer": {"@type": "Answer", "text": a}}
            for q, a in FAQ]},
        {"@type": "ItemList", "@id": URL + "#perfumes", "name": "عطور باهي والعطور العالمية المستوحاة منها",
         "itemListElement": [
             {"@type": "ListItem", "position": i, "name": f"{p['name']} (مستوحى من {en})",
              "url": f"{config.STORE_URL}/products/{p['handle']}"}
             for i, (p, en, _) in enumerate(_inspired(), 1)]},
    ]
    data = json.dumps({"@context": "https://schema.org", "@graph": graph}, ensure_ascii=False)
    # Safe inside <script> and inside a Liquid response.
    data = data.replace("</", "<\\/").replace("{{", "{ {").replace("{%", "{ %")
    return f'<script type="application/ld+json">{data}</script>'


GUIDE = guide_html()
JSON_LD = json_ld()
assert "{{" not in GUIDE and "{%" not in GUIDE, "guide text must not contain Liquid tags"
