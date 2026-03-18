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


# ──────────────────────────────────────────────
# Albert Heijn — native GraphQL API
# ──────────────────────────────────────────────

AH_TOKEN_URL   = "https://api.ah.nl/mobile-auth/v1/auth/token/anonymous"
AH_GRAPHQL_URL = "https://api.ah.nl/graphql"

AH_QUERY = """
{
  bonusPromotions(promotionType: NATIONAL) {
    id
    title
    subtitle
    promotionType
    price {
      now { amount }
      was { amount }
    }
    products {
      title
      category
      price {
        now { amount }
        was { amount }
      }
      images { url }
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
                prod_price = product.get("price") or {}
                if prod_price.get("now"):
                    price_now = str(prod_price["now"]["amount"])
                if prod_price.get("was"):
                    price_was = str(prod_price["was"]["amount"])

                # Fall back to promotion-level price if product has none
                if price_now is None:
                    promo_price = promo.get("price") or {}
                    if promo_price.get("now"):
                        price_now = str(promo_price["now"]["amount"])
                    if promo_price.get("was"):
                        price_was = str(promo_price["was"]["amount"])

                img = ""
                images = product.get("images") or []
                if images:
                    img = images[0].get("url", "")

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

            promo_price = promo.get("price") or {}
            price_now = str(promo_price["now"]["amount"]) if promo_price.get("now") else None
            price_was = str(promo_price["was"]["amount"]) if promo_price.get("was") else None

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
