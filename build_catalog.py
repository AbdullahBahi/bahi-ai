# -*- coding: utf-8 -*-
"""Regenerate catalog.json, the Bahi perfume knowledge the AI advisor is given.

Sources of truth (same as the store's own product pages):
  ../products_data.py                                   names, notes, seasons, copy
  ../../Products/Batch1 Products - BHP260921-01/reviews/  Fragrantica votes for each original

Run after changing products_data.py or the vote CSVs, then commit catalog.json:
    python build_catalog.py
Prices and stock are not stored here: the app reads them live from the storefront.
"""
import csv, json, os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
SHOPIFY_DIR = os.path.dirname(HERE)
REVIEWS = os.path.join(SHOPIFY_DIR, "..", "Products", "Batch1 Products - BHP260921-01", "reviews")
OUT = os.path.join(HERE, "catalog.json")

sys.path.insert(0, SHOPIFY_DIR)
from products_data import PRODUCTS, SIZES  # noqa: E402

# Same scales and labels as ../set_performance.py (what the product pages show).
LONGEVITY = [("lon_very_weak", "ضعيف جدًا", "أقل من ساعة"),
             ("lon_weak", "ضعيف", "1–2 ساعة"),
             ("lon_moderate", "متوسط", "3–6 ساعات"),
             ("lon_long_lasting", "طويل", "7–12 ساعة"),
             ("lon_eternal", "طويل جدًا", "أكثر من 12 ساعة")]
SILLAGE = [("sil_intimate", "قريب من البشرة", "يُشمّ عن قرب"),
           ("sil_moderate", "متوسط", "يُشمّ على مسافة ذراع"),
           ("sil_strong", "قوي", "يُشمّ على بُعد خطوات"),
           ("sil_enormous", "قوي جدًا", "يملأ المكان")]
RATING = ["hate", "dislike", "ok", "like", "love"]


def weighted(row, keys):
    counts = [int(row[k]) for k in keys]
    return sum((i + 1) * n for i, n in enumerate(counts)) / sum(counts)


def level(row, scale):
    avg = weighted(row, [k for k, _, _ in scale])
    _, label, hint = scale[round(avg) - 1]
    return f"{label} ({hint})"


def read_csv(name):
    with open(os.path.join(REVIEWS, name), encoding="utf-8") as f:
        return {r["handle"]: r for r in csv.DictReader(f)}


def main():
    perf = read_csv("performance_votes.csv")
    ratings = read_csv("rating_votes.csv")
    items = []
    for p in PRODUCTS:
        h = p["handle"]
        item = {
            "handle": h,
            "name": p["name"],
            "name_voweled": p["name_voweled"],
            "family": p["family"],
            "gender": p["gender"],
            "inspired_by": p["inspired"],
            "tagline": p["tagline"],
            "top": p["top"], "heart": p["mid"], "base": p["base"],
            "seasons": p["seasons"], "usage": p["usage"],
            "season_note": p["season_note"],
            "best_for": p["best_for"],
            "occasions": p["occasions"],
            "story": p["story"],
        }
        if h in perf:
            item["original_longevity"] = level(perf[h], LONGEVITY)
            item["original_sillage"] = level(perf[h], SILLAGE)
        r = ratings.get(h)
        if r:
            rating = r["rating"] or f"{weighted(r, RATING):.2f}"
            item["original_rating"] = f"{float(rating):.1f}/5"
        items.append(item)

    data = {
        "sizes": [{"label": s[0], "default_price": s[1]} for s in SIZES],
        "products": items,
    }
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    print(f"wrote {OUT} ({len(items)} products)")


if __name__ == "__main__":
    main()
