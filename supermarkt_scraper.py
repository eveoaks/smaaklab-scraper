#!/usr/bin/env python3
"""
Smaaklab Supermarkt Scraper
Scrapes wekelijkse aanbiedingen van supermarktaanbiedingen.com
Output: deals.json
"""

import json
import re
import time
from datetime import date

import requests

SUPERMARKTEN = [
    ("Albert Heijn", "albert_heijn"),
    ("Jumbo",        "jumbo"),
    ("Lidl",         "lidl"),
    ("Aldi",         "aldi"),
    ("Plus",         "plus"),
    ("Dirk",         "dirk"),
]

BASE_URL = "https://www.supermarktaanbiedingen.com/aanbiedingen/{slug}"
HEADERS  = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    "Accept-Language": "nl-NL,nl;q=0.9,en;q=0.8",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
    "Cache-Control": "max-age=0",
    "Upgrade-Insecure-Requests": "1",
}
TIMEOUT  = 15


def scrape_supermarkt(naam, slug):
    url = BASE_URL.format(slug=slug)
    try:
        r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
        r.raise_for_status()
        html = r.text
    except Exception as e:
        print(f"  FOUT {naam}: {e}")
        return []

    cards = re.findall(
        r'<div class="card[^"]*"><a class="product-title"[^>]*href="([^"]+)"[^>]*>(.*?)</a>',
        html, re.DOTALL
    )

    seen = set()
    results = []

    for href, content in cards:
        title_m = re.search(r'<h3 class="card_title">([^<]+)</h3>', content)
        desc_m  = re.search(r'<p class="card_text"[^>]*>([^<]+)</p>', content)
        price_m = re.search(r'<span class="card_prijs">([^<]+)</span>', content)
        was_m   = re.search(r'<span class="card_prijs-oud">([^<]+)</span>', content)
        img_m   = re.search(r'<img[^>]+src="([^"]+)"', content)

        naam_product = title_m.group(1).strip() if title_m else ""
        if not naam_product:
            continue

        # Dedup op naam
        key = naam_product.lower()
        if key in seen:
            continue
        seen.add(key)

        def clean_price(raw):
            if not raw:
                return None
            cleaned = re.sub(r"&nbsp;|&euro;|&[^;]+;", "", raw).strip()
            cleaned = re.sub(r"\s+", " ", cleaned).strip()
            return cleaned if cleaned else None

        prijs = clean_price(price_m.group(1) if price_m else "")
        was   = clean_price(was_m.group(1)   if was_m   else "")

        results.append({
            "supermarkt": naam,
            "naam":  naam_product,
            "desc":  desc_m.group(1).strip() if desc_m else "",
            "prijs": prijs,
            "was":   was,
            "img":   img_m.group(1) if img_m else "",
            "url":   "https://www.supermarktaanbiedingen.com" + href,
        })

    return results


def main():
    all_deals = []

    for naam, slug in SUPERMARKTEN:
        print(f"Scraping {naam}...")
        deals = scrape_supermarkt(naam, slug)
        print(f"  {len(deals)} deals gevonden")
        all_deals.extend(deals)
        time.sleep(1)  # Beleefd wachten tussen requests

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
