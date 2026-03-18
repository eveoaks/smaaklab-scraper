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

FOOD_CATEGORIES = {
    "Bakkerij",
    "Frisdrank, sappen, water",
    "Bier, wijn, aperitieven",
    "Borrel, chips, snacks",
    "Koffie, thee",
    "Koek, snoep, chocolade",
    "Pasta, rijst, wereldkeuken",
    "Zuivel, eieren",
    "Maaltijden, salades",
    "Kaas",
    "Ontbijtgranen, beleg",
    "Soepen, sauzen, kruiden, olie",
    "Fruit, verse sappen",
    "Diepvries",
    "Groente, aardappelen",
    "Vis",
    "Vegetarisch, vegan en plantaardig",
    "Vleeswaren",
    "Vlees",
    "Glutenvrij",
    "Tussendoortjes",
}


def is_food(deal):
    main_cat = deal.get("desc", "").split("/")[0].strip()
    return main_cat in FOOD_CATEGORIES


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
# Main
# ──────────────────────────────────────────────

SCRAPERS = [
    ("Albert Heijn", scrape_ah),
]


def main():
    all_deals = []

    for naam, scraper in SCRAPERS:
        print(f"Scraping {naam}...")
        deals = scraper()
        deals = [d for d in deals if is_food(d) and d.get("was")]
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
