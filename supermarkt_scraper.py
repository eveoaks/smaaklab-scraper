#!/usr/bin/env python3
"""
Smaaklab Supermarkt Scraper
Haalt wekelijkse aanbiedingen op via native supermarkt APIs.
Output: deals.json
"""

import json
import time
from datetime import date

import requests

TIMEOUT = 15

# Gemeenschappelijke categorieën waarop gefilterd wordt in de viewer
CATEGORY_MAP = {
    # AH
    "Bakkerij":                          "Brood & gebak",
    "Frisdrank, sappen, water":          "Frisdrank",
    "Bier, wijn, aperitieven":           "Bier & wijn",
    "Borrel, chips, snacks":             "Snacks",
    "Koffie, thee":                      "Koffie & thee",
    "Koek, snoep, chocolade":            "Koek & snoep",
    "Pasta, rijst, wereldkeuken":        "Pasta & rijst",
    "Zuivel, eieren":                    "Zuivel & eieren",
    "Maaltijden, salades":               "Maaltijden",
    "Kaas":                              "Vleeswaren & kaas",
    "Ontbijtgranen, beleg":              "Ontbijt & beleg",
    "Soepen, sauzen, kruiden, olie":     "Soepen & sauzen",
    "Fruit, verse sappen":               "Groente & fruit",
    "Diepvries":                         "Diepvries",
    "Groente, aardappelen":              "Groente & fruit",
    "Vis":                               "Vlees & vis",
    "Vegetarisch, vegan en plantaardig": "Vegetarisch & vegan",
    "Vleeswaren":                        "Vleeswaren & kaas",
    "Vlees":                             "Vlees & vis",
    "Glutenvrij":                        "Glutenvrij",
    "Tussendoortjes":                    "Snacks",
    # Jumbo
    "Bier en wijn":                              "Bier & wijn",
    "Zuivel, eieren, boter":                     "Zuivel & eieren",
    "Conserven, soepen, sauzen, oliën":          "Soepen & sauzen",
    "Frisdrank en sappen":                       "Frisdrank",
    "Koffie en thee":                            "Koffie & thee",
    "Koek, snoep, chocolade en chips":           "Koek & snoep",
    "Aardappelen, groente en fruit":             "Groente & fruit",
    "Vlees, vis en vega":                        "Vlees & vis",
    "Vleeswaren, kaas en tapas":                 "Vleeswaren & kaas",
    "Brood en gebak":                            "Brood & gebak",
    "Ontbijt, broodbeleg en bakproducten":       "Ontbijt & beleg",
    "Verse maaltijden en gemak":                 "Maaltijden",
    "Pasta, rijst en wereldkeuken":              "Pasta & rijst",
}

NON_FOOD = {
    "Huishouden", "Drogisterij", "Pasen", "Gezondheid en sport",
    "Baby en kind", "Huisdier", "AH Voordeelshop",
    "Drogisterij en baby", "Huishouden en schoonmaak",
    "Non-food", "Elektronica en multimedia", "Kantoor en school",
}


def normalize_category(raw_cat):
    main = raw_cat.split("/")[0].strip()
    return CATEGORY_MAP.get(main)  # None = niet-eten, overgeslagen


def is_food(deal):
    return normalize_category(deal.get("desc", "")) is not None


# ──────────────────────────────────────────────
# Albert Heijn — native GraphQL API
# ──────────────────────────────────────────────

AH_TOKEN_URL   = "https://api.ah.nl/mobile-auth/v1/auth/token/anonymous"
AH_GRAPHQL_URL = "https://api.ah.nl/graphql"

AH_QUERY = """
{
  bonusPromotions {
    id
    title
    subtitle
    promotionType
    products {
      title
      category
      priceV2 {
        now { amount }
        was { amount }
      }
      imagePack {
        small { url }
      }
    }
  }
}
"""


def ah_get_token():
    r = requests.post(
        AH_TOKEN_URL,
        json={"clientId": "appie"},
        timeout=TIMEOUT,
    )
    r.raise_for_status()
    return r.json()["access_token"]


def scrape_ah():
    try:
        token = ah_get_token()
    except Exception as e:
        print(f"  FOUT Albert Heijn (token): {e}")
        return []

    headers = {
        "Authorization": f"Bearer {token}",
        "x-application": "AHWEBSHOP",
        "Content-Type": "application/json",
    }

    try:
        r = requests.post(
            AH_GRAPHQL_URL,
            json={"query": AH_QUERY},
            headers=headers,
            timeout=TIMEOUT,
        )
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        print(f"  FOUT Albert Heijn (graphql): {e}")
        return []

    promotions = data.get("data", {}).get("bonusPromotions", [])
    results = []
    seen = set()

    for promo in promotions:
        if promo.get("promotionType") != "NATIONAL":
            continue
        products = promo.get("products") or []

        if products:
            for product in products:
                naam = (product.get("title") or "").strip()
                if not naam:
                    continue
                key = naam.lower()
                if key in seen:
                    continue
                seen.add(key)

                price_now = None
                price_was = None
                prod_price = product.get("priceV2") or {}
                if prod_price.get("now"):
                    price_now = str(prod_price["now"]["amount"])
                if prod_price.get("was"):
                    price_was = str(prod_price["was"]["amount"])

                img = ""
                image_packs = product.get("imagePack") or []
                if image_packs:
                    small = (image_packs[0] or {}).get("small") or {}
                    img = small.get("url", "")

                results.append({
                    "supermarkt": "Albert Heijn",
                    "naam":  naam,
                    "desc":  (product.get("category") or "").strip(),
                    "prijs": price_now,
                    "was":   price_was,
                    "img":   img,
                    "url":   "https://www.ah.nl/bonus",
                })
        else:
            # Promotion without individual products — use promo title
            naam = (promo.get("title") or "").strip()
            if not naam:
                continue
            key = naam.lower()
            if key in seen:
                continue
            seen.add(key)

            price_now = None
            price_was = None

            results.append({
                "supermarkt": "Albert Heijn",
                "naam":  naam,
                "desc":  (promo.get("subtitle") or "").strip(),
                "prijs": price_now,
                "was":   price_was,
                "img":   "",
                "url":   "https://www.ah.nl/bonus",
            })

    return results


# ──────────────────────────────────────────────
# Jumbo — GraphQL API
# ──────────────────────────────────────────────

JUMBO_GQL_URL = "https://www.jumbo.com/api/graphql"
JUMBO_HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Content-Type": "application/json",
    "apollographql-client-name": "JUMBO_WEB",
    "apollographql-client-version": "master-v30.12.0-web",
}

JUMBO_FOOD_CATEGORIES = {
    "Groente en fruit",
    "Vleeswaren, kaas en tapas",
    "Brood en gebak",
    "Zuivel, eieren en boter",
    "Salades, maaltijden en gemak",
    "Vis",
    "Vlees, kip en vis",
    "Diepvries",
    "Frisdrank, sappen en water",
    "Koffie, thee en cacao",
    "Bier, wijn en aperitieven",
    "Koek, snoep en chips",
    "Pasta, rijst en wereldkeuken",
    "Soepen, sauzen en kruiden",
    "Ontbijt en beleg",
    "Vegetarisch en vegan",
    "Biologisch",
    "Glutenvrij",
}


def scrape_jumbo():
    # Stap 1: haal alle Week-promoties op voor hun SKUs
    try:
        r = requests.post(
            JUMBO_GQL_URL,
            json={"query": "query { promotions { group products { sku } } }"},
            headers=JUMBO_HEADERS,
            timeout=TIMEOUT,
        )
        r.raise_for_status()
        promos = r.json().get("data", {}).get("promotions", []) or []
    except Exception as e:
        print(f"  FOUT Jumbo (promotions): {e}")
        return []

    week_skus = list({
        prod["sku"]
        for p in promos
        if p.get("group") == "Week"
        for prod in p.get("products", [])
    })

    if not week_skus:
        return []

    # Stap 2: haal productdetails op in batches van 50
    all_prods = []
    batch_size = 50
    for i in range(0, len(week_skus), batch_size):
        batch = week_skus[i:i + batch_size]
        query = (
            "query { products(skus: " + json.dumps(batch) + ") "
            "{ sku title price { price promoPrice } image rootCategory } }"
        )
        try:
            r2 = requests.post(
                JUMBO_GQL_URL,
                json={"query": query},
                headers=JUMBO_HEADERS,
                timeout=TIMEOUT,
            )
            r2.raise_for_status()
            all_prods.extend(r2.json().get("data", {}).get("products", []) or [])
        except Exception as e:
            print(f"  FOUT Jumbo (products batch {i}): {e}")
        time.sleep(0.3)

    results = []
    seen = set()

    for prod in all_prods:
        naam = (prod.get("title") or "").strip()
        if not naam:
            continue
        key = naam.lower()
        if key in seen:
            continue
        seen.add(key)

        price_data = prod.get("price") or {}
        price_now = price_data.get("promoPrice")
        price_was = price_data.get("price")

        # Alleen echte kortingen bewaren
        if not price_now or not price_was or price_now >= price_was:
            continue

        cat = (prod.get("rootCategory") or "").strip()

        results.append({
            "supermarkt": "Jumbo",
            "naam":  naam,
            "desc":  cat,
            "prijs": str(round(price_now / 100, 2)),
            "was":   str(round(price_was / 100, 2)),
            "img":   prod.get("image") or "",
            "url":   "https://www.jumbo.com/aanbiedingen/alle-aanbiedingen/",
        })

    return results


# ──────────────────────────────────────────────
# Main
# ──────────────────────────────────────────────

SCRAPERS = [
    ("Albert Heijn", scrape_ah),
    ("Jumbo",        scrape_jumbo),
]


def main():
    all_deals = []

    for naam, scraper in SCRAPERS:
        print(f"Scraping {naam}...")
        deals = scraper()
        deals = [d for d in deals if is_food(d) and d.get("was")]
        for d in deals:
            d["desc"] = normalize_category(d["desc"])
        print(f"  {len(deals)} deals gevonden")
        all_deals.extend(deals)
        time.sleep(1)

    output = {
        "datum":  str(date.today()),
        "totaal": len(all_deals),
        "deals":  all_deals,
    }

    with open("deals.json", "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"\nKlaar: {len(all_deals)} deals opgeslagen in deals.json")


if __name__ == "__main__":
    main()
