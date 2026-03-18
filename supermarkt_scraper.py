#!/usr/bin/env python3
"""
Smaaklab Supermarkt Scraper
Haalt wekelijkse aanbiedingen op via native supermarkt APIs.
Output: deals.json
"""

import base64
import json
import os
import re
import time
from datetime import date

import anthropic
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
    # Lidl
    "Weekaanbieding":                    "Weekaanbieding",
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
        promotionLabel {
          tiers { mechanism description count freeCount price percentage }
        }
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

        if not products:
            continue

        for product in products:
            naam = (product.get("title") or "").strip()
            if not naam:
                continue
            key = naam.lower()
            if key in seen:
                continue
            seen.add(key)

            prod_price = product.get("priceV2") or {}
            now_amt  = (prod_price.get("now")  or {}).get("amount")
            was_amt  = (prod_price.get("was")  or {}).get("amount")

            promo_label = prod_price.get("promotionLabel") or {}
            tiers = promo_label.get("tiers") or []
            tier = tiers[0] if tiers else {}
            mechanism = tier.get("mechanism", "")

            # Compute deal_label + was + effectief_prijs from tier mechanism
            deal_label = None
            effectief_prijs = None

            if mechanism in ("FIXED_PRICE", "AMOUNT", "WEIGHT"):
                # Simple price reduction — show % korting instead of redundant price label
                if now_amt and was_amt and now_amt < was_amt:
                    pct = round((1 - now_amt / was_amt) * 100)
                    deal_label = f"{pct}% korting"
                else:
                    deal_label = tier.get("description")

            elif mechanism == "PERCENTAGE":
                deal_label = tier.get("description")  # Already "25% korting"

            elif mechanism == "ONE_HALF_PRICE":
                deal_label = tier.get("description") or "2e halve prijs"
                # now_amt is the per-item regular price; was may be None
                if now_amt:
                    effectief_prijs = round(now_amt * 1.5 / 2, 2)  # avg of (1 + 0.5) items
                if was_amt is None:
                    was_amt = now_amt  # set was = normal price for filter

            elif mechanism == "X_PLUS_Y_FREE":
                deal_label = tier.get("description") or "1+1 gratis"
                count      = tier.get("count") or 1
                free_count = tier.get("freeCount") or 1
                if now_amt:
                    effectief_prijs = round(now_amt * count / (count + free_count), 2)
                if was_amt is None:
                    was_amt = now_amt

            elif mechanism == "X_FOR_Y":
                deal_label  = tier.get("description")  # "2 voor 2.49"
                tier_price  = tier.get("price")
                count       = tier.get("count")
                if tier_price and count:
                    effectief_prijs = round(tier_price / count, 2)
                if was_amt is None:
                    was_amt = now_amt

            else:
                # Unknown / no mechanism — use description if we have one
                deal_label = tier.get("description")

            # Skip deals with no price reduction and no deal info
            # (e.g. "gratis bezorging" bundles where prijs == was == regular price)
            if now_amt and was_amt and now_amt == was_amt and not deal_label:
                continue

            img = ""
            image_packs = product.get("imagePack") or []
            if image_packs:
                small = (image_packs[0] or {}).get("small") or {}
                img = small.get("url", "")

            results.append({
                "supermarkt":    "Albert Heijn",
                "naam":          naam,
                "desc":          (product.get("category") or "").strip(),
                "prijs":         str(now_amt)  if now_amt  is not None else None,
                "was":           str(was_amt)  if was_amt  is not None else None,
                "deal_label":    deal_label,
                "effectief":     str(effectief_prijs) if effectief_prijs is not None else None,
                "img":           img,
                "url":           "https://www.ah.nl/bonus",
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

        # Jumbo promoPrice is the effective per-item price (already averaged for 2e-deals)
        # Derive deal type from price ratio
        ratio = price_now / price_was if price_was else 1
        effectief_prijs = None
        if abs(ratio - 0.75) < 0.03:
            deal_label = "2e halve prijs"
            # promoPrice IS the per-item avg (0.75 * was) when buying 2
        elif abs(ratio - 0.67) < 0.03:
            deal_label = "2e voor 1/3 prijs"
            # promoPrice IS the per-item avg (2/3 * was) when buying 2
        elif price_now == 0:
            deal_label = "1+1 gratis"
        else:
            korting_pct = round((1 - ratio) * 100)
            deal_label = f"{korting_pct}% korting"

        results.append({
            "supermarkt":  "Jumbo",
            "naam":        naam,
            "desc":        cat,
            "prijs":       str(round(price_now / 100, 2)),
            "was":         str(round(price_was / 100, 2)),
            "deal_label":  deal_label,
            "effectief":   str(effectief_prijs) if effectief_prijs is not None else None,
            "img":         prod.get("image") or "",
            "url":         "https://www.jumbo.com/aanbiedingen/alle-aanbiedingen/",
        })

    return results


# ──────────────────────────────────────────────
# Lidl — folderz.nl + Claude Vision
# ──────────────────────────────────────────────

FOLDERZ_LIST_URL   = "https://www.folderz.nl/winkels/lidl/aanbiedingen"
FOLDERZ_FOLDER_URL = "https://www.folderz.nl/bekijk/aanbiedingen/lidl-folder-{folder_id}"
FOLDERZ_HEADERS    = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    "Accept-Language": "nl-NL,nl;q=0.9",
}

LIDL_VISION_PROMPT = (
    "Dit is een pagina uit de Lidl weekfolder. "
    "Extraheer ALLEEN voedselproducten (geen huishoudartikelen, drogisterij, kleding, elektronica, schoonmaakmiddelen).\n\n"
    "Geef de output als een JSON array, één object per product:\n"
    '[{"naam": "productnaam", "prijs": "1.99", "was": "2.99", "deal_label": "34% korting"}]\n\n'
    "Regels:\n"
    '- "naam": volledige productnaam inclusief merk, variant en gewicht/inhoud (bijv "Arla Skyr Aardbei 450g")\n'
    '- "prijs": de aanbiedingsprijs als decimaal getal (bijv "1.99")\n'
    '- "was": de originele prijs als die zichtbaar is, anders null\n'
    '- "deal_label": zichtbare aanbiedingstekst zoals "34% korting", "2e halve prijs", "1+1 gratis", "2 voor 3.00" — of lege string\n'
    "- Als er geen duidelijke prijs staat, sla het product over\n"
    "- Geef ALLEEN de JSON array terug, geen extra tekst, geen markdown"
)


def _lidl_get_folder_id():
    r = requests.get(FOLDERZ_LIST_URL, headers=FOLDERZ_HEADERS, timeout=TIMEOUT)
    r.raise_for_status()
    print(f"  Folderz response: {r.status_code}, {len(r.text)} tekens, 'lidl-folder' aanwezig: {'lidl-folder' in r.text}")
    # URL staat in JSON-LD als escaped slashes: \/bekijk\/aanbiedingen\/lidl-folder-XXXXXXX
    matches = re.findall(r'lidl-folder-(\d+)', r.text)
    if not matches:
        raise ValueError("Geen Lidl folder-ID gevonden op folderz.nl")
    return matches[0]  # eerste = meest recent (hoogste ID)


def _lidl_get_page_urls(folder_id):
    url = FOLDERZ_FOLDER_URL.format(folder_id=folder_id)
    r = requests.get(url, headers=FOLDERZ_HEADERS, timeout=TIMEOUT)
    r.raise_for_status()
    pattern = (
        r'https://img\.offers-cdn\.net/assets/uploads/flyers/'
        + re.escape(folder_id)
        + r'/260x270WebP/lidl-(\d+)-1-([a-zA-Z0-9_-]+)\.webp'
    )
    img_matches = re.findall(pattern, r.text)
    seen = set()
    page_urls = []
    for page_num, hash_ in img_matches:
        if page_num not in seen:
            seen.add(page_num)
            thumb_url = (
                f"https://img.offers-cdn.net/assets/uploads/flyers/"
                f"{folder_id}/260x270WebP/lidl-{page_num}-1-{hash_}.webp"
            )
            # Hogere resolutie voor betere leesbaarheid door Claude
            hires_url = (
                f"https://img.offers-cdn.net/assets/uploads/flyers/"
                f"{folder_id}/800x830WebP/lidl-{page_num}-1-{hash_}.webp"
            )
            page_urls.append((int(page_num), hires_url, thumb_url))
    page_urls.sort()
    return page_urls


def _parse_claude_products(text):
    """Parse Claude JSON response, strip markdown code fences and trailing text."""
    text = text.strip()
    text = re.sub(r'^```(?:json)?\s*', '', text)
    text = re.sub(r'\s*```$', '', text)
    # Pak alleen het stuk tussen de eerste [ en de laatste ]
    start = text.find('[')
    end = text.rfind(']')
    if start == -1 or end == -1:
        raise json.JSONDecodeError("Geen JSON array gevonden", text, 0)
    return json.loads(text[start:end + 1])


def scrape_lidl():
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        print("  FOUT Lidl: ANTHROPIC_API_KEY niet ingesteld, Lidl overgeslagen")
        return []

    try:
        folder_id = _lidl_get_folder_id()
        print(f"  Lidl folder-ID: {folder_id}")
    except Exception as e:
        print(f"  FOUT Lidl (folder list): {e}")
        return []

    try:
        page_urls = _lidl_get_page_urls(folder_id)
        print(f"  Lidl: {len(page_urls)} pagina's gevonden")
    except Exception as e:
        print(f"  FOUT Lidl (folder pagina's): {e}")
        return []

    client = anthropic.Anthropic(api_key=api_key)
    folder_url = FOLDERZ_FOLDER_URL.format(folder_id=folder_id)
    results = []
    seen = set()

    for page_num, hires_url, thumb_url in page_urls:
        try:
            # Probeer hoge resolutie; val terug op lage resolutie bij 404
            img_r = requests.get(hires_url, timeout=TIMEOUT)
            if img_r.status_code == 404:
                img_r = requests.get(thumb_url, timeout=TIMEOUT)
            img_r.raise_for_status()
            img_b64 = base64.standard_b64encode(img_r.content).decode("utf-8")

            response = client.messages.create(
                model="claude-sonnet-4-6",
                max_tokens=2048,
                messages=[{
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": "image/webp",
                                "data": img_b64,
                            },
                        },
                        {"type": "text", "text": LIDL_VISION_PROMPT},
                    ],
                }],
            )

            products = _parse_claude_products(response.content[0].text)

            for prod in products:
                naam = (prod.get("naam") or "").strip()
                prijs_str = str(prod.get("prijs") or "").strip().replace(",", ".")
                if not naam or not prijs_str:
                    continue
                try:
                    float(prijs_str)
                except ValueError:
                    continue
                key = naam.lower()
                if key in seen:
                    continue
                seen.add(key)

                was_raw = prod.get("was")
                was_str = str(was_raw).replace(",", ".") if was_raw else None
                try:
                    if was_str:
                        float(was_str)
                except ValueError:
                    was_str = None

                deal_label = (prod.get("deal_label") or "").strip() or "Weekaanbieding"

                results.append({
                    "supermarkt": "Lidl",
                    "naam":       naam,
                    "desc":       "Weekaanbieding",
                    "prijs":      prijs_str,
                    "was":        was_str,
                    "deal_label": deal_label,
                    "effectief":  None,
                    "img":        thumb_url,
                    "url":        folder_url,
                })

        except json.JSONDecodeError as e:
            print(f"  Lidl pagina {page_num}: JSON parse fout – {e}")
        except Exception as e:
            print(f"  Lidl pagina {page_num}: {e}")
        time.sleep(0.4)

    return results


# ──────────────────────────────────────────────
# Main
# ──────────────────────────────────────────────

SCRAPERS = [
    ("Albert Heijn", scrape_ah),
    ("Jumbo",        scrape_jumbo),
    ("Lidl",         scrape_lidl),
]


HTML_TEMPLATE = """\
<!DOCTYPE html>
<html lang="nl">
<head>
<meta charset="UTF-8">
<title>Supermarkt Deals</title>
<style>
  body {{ font-family: sans-serif; max-width: 1000px; margin: 0 auto; padding: 20px; background: #f5f5f5; }}
  h1 {{ color: #333; margin-bottom: 4px; }}
  .datum {{ color: #888; font-size: 13px; margin-bottom: 16px; }}
  .filters {{ margin-bottom: 12px; display: flex; gap: 10px; flex-wrap: wrap; align-items: center; }}
  input[type=text] {{ padding: 8px 12px; border: 1px solid #ccc; border-radius: 6px; font-size: 14px; width: 240px; }}
  select {{ padding: 8px 12px; border: 1px solid #ccc; border-radius: 6px; font-size: 14px; }}
  .tag-filters {{ display: flex; flex-wrap: wrap; gap: 6px; margin-bottom: 8px; }}
  .tag-btn {{ padding: 5px 12px; border: 1px solid #ccc; border-radius: 20px; font-size: 12px; cursor: pointer; background: white; color: #444; transition: all 0.15s; }}
  .tag-btn:hover {{ border-color: #888; }}
  .tag-btn.active {{ background: #222; color: white; border-color: #222; }}
  .count {{ color: #666; font-size: 14px; margin-bottom: 12px; }}
  table {{ width: 100%; border-collapse: collapse; background: white; border-radius: 8px; overflow: hidden; box-shadow: 0 1px 4px rgba(0,0,0,0.1); }}
  th {{ background: #222; color: white; padding: 10px 12px; text-align: left; font-size: 13px; }}
  td {{ padding: 10px 12px; border-bottom: 1px solid #eee; font-size: 13px; vertical-align: middle; }}
  tr:last-child td {{ border-bottom: none; }}
  tr:hover td {{ background: #f9f9f9; }}
  .img-cell img {{ width: 48px; height: 48px; object-fit: contain; border-radius: 4px; }}
  .badge {{ display: inline-block; background: #e8f5e9; color: #2e7d32; font-weight: bold; padding: 2px 7px; border-radius: 10px; font-size: 12px; }}
  .badge-deal {{ background: #fff3e0; color: #e65100; }}
  .was {{ color: #999; text-decoration: line-through; font-size: 12px; }}
  .prijs {{ font-weight: bold; color: #111; }}
  .cat {{ color: #888; font-size: 12px; }}
  .super-tag {{ display: inline-block; padding: 1px 6px; border-radius: 4px; font-size: 11px; font-weight: bold; }}
  .super-ah {{ background: #e3f4fc; color: #007dc5; }}
  .super-jumbo {{ background: #fff8dc; color: #b8860b; }}
  .super-lidl {{ background: #fff0f0; color: #c0392b; }}
  .effectief {{ font-weight: bold; color: #2e7d32; }}
  .effectief small {{ font-weight: normal; color: #888; font-size: 11px; }}
</style>
</head>
<body>
<h1>Supermarkt Deals</h1>
<p class="datum">Bijgewerkt: {DATUM}</p>
<div class="filters">
  <input type="text" id="search" placeholder="Zoeken...">
  <select id="sortBy">
    <option value="korting">Hoogste korting %</option>
    <option value="prijs">Laagste prijs</option>
    <option value="naam">Naam A-Z</option>
  </select>
</div>
<div class="tag-filters" id="superFilters"></div>
<div class="tag-filters" id="catFilters"></div>
<div class="count" id="count"></div>
<table>
  <thead><tr><th></th><th>Product</th><th>Winkel</th><th>Categorie</th><th>Nu</th><th>Was</th><th>Aanbieding</th><th>Effectief/stuk</th></tr></thead>
  <tbody id="tbody"></tbody>
</table>
<script>
const deals = {DEALS_JSON};
const cats = {CATS_JSON};
const supers = {SUPERS_JSON};
let activeCat = null, activeSuper = null;

function makeBtn(label, cssClass, onClick) {{
  const btn = document.createElement('button');
  btn.className = 'tag-btn' + (cssClass ? ' ' + cssClass : '');
  btn.textContent = label;
  btn.onclick = onClick;
  return btn;
}}

const sf = document.getElementById('superFilters');
const allSBtn = makeBtn('Alle winkels', 'active', () => {{ activeSuper = null; setActive(sf, allSBtn); render(); }});
sf.appendChild(allSBtn);
supers.forEach(s => {{
  const btn = makeBtn(s, '', () => {{ activeSuper = s; setActive(sf, btn); render(); }});
  sf.appendChild(btn);
}});

const cf = document.getElementById('catFilters');
const allCBtn = makeBtn('Alle categorieën', 'active', () => {{ activeCat = null; setActive(cf, allCBtn); render(); }});
cf.appendChild(allCBtn);
cats.forEach(cat => {{
  const btn = makeBtn(cat, '', () => {{ activeCat = cat; setActive(cf, btn); render(); }});
  cf.appendChild(btn);
}});

function setActive(container, el) {{
  container.querySelectorAll('.tag-btn').forEach(b => b.classList.remove('active'));
  el.classList.add('active');
}}

function render() {{
  const q = document.getElementById('search').value.toLowerCase();
  const sort = document.getElementById('sortBy').value;
  let filtered = deals.filter(d =>
    (!activeCat || d.desc === activeCat) &&
    (!activeSuper || d.supermarkt === activeSuper) &&
    (d.naam.toLowerCase().includes(q) || d.desc.toLowerCase().includes(q) || d.supermarkt.toLowerCase().includes(q))
  );
  if (sort === 'korting') filtered.sort((a, b) => b.korting - a.korting);
  else if (sort === 'prijs') filtered.sort((a, b) => parseFloat(a.prijs || a.was) - parseFloat(b.prijs || b.was));
  else filtered.sort((a, b) => a.naam.localeCompare(b.naam));
  document.getElementById('count').textContent = filtered.length + ' deals';
  const superClass = s => s === 'Albert Heijn' ? 'super-ah' : s === 'Jumbo' ? 'super-jumbo' : s === 'Lidl' ? 'super-lidl' : '';
  const isPct = label => label && /^\\d+%/.test(label);
  document.getElementById('tbody').innerHTML = filtered.map(d => {{
    const label = d.deal_label || '';
    const badgeClass = isPct(label) ? 'badge' : 'badge badge-deal';
    const prijsVal = parseFloat(d.prijs || d.was);
    const wasCell  = d.was ? `€${{parseFloat(d.was).toFixed(2)}}` : '';
    const effectiefCell = d.effectief
      ? `<span class="effectief">€${{parseFloat(d.effectief).toFixed(2)}}<small> bij 2</small></span>`
      : '';
    return `<tr>
      <td class="img-cell">${{d.img ? `<img src="${{d.img}}" alt="">` : ''}}</td>
      <td><strong>${{d.naam}}</strong></td>
      <td><span class="super-tag ${{superClass(d.supermarkt)}}">${{d.supermarkt}}</span></td>
      <td class="cat">${{d.desc}}</td>
      <td class="prijs">€${{prijsVal.toFixed(2)}}</td>
      <td class="was">${{wasCell}}</td>
      <td><span class="${{badgeClass}}">${{label || '—'}}</span></td>
      <td>${{effectiefCell}}</td>
    </tr>`;
  }}).join('');
}}
document.getElementById('search').addEventListener('input', render);
document.getElementById('sortBy').addEventListener('change', render);
render();
</script>
</body>
</html>"""


def generate_viewer(all_deals, datum):
    # Bereken korting% voor sortering
    enriched = []
    for d in all_deals:
        d = dict(d)
        try:
            prijs = float(d.get("prijs") or d.get("was") or 0)
            was = float(d.get("was") or 0)
            d["korting"] = round((1 - prijs / was) * 100) if was and prijs < was else 0
        except (ValueError, ZeroDivisionError):
            d["korting"] = 0
        enriched.append(d)

    cats = sorted({d["desc"] for d in enriched if d.get("desc")})
    supers = sorted({d["supermarkt"] for d in enriched if d.get("supermarkt")})

    html = HTML_TEMPLATE.format(
        DATUM=datum,
        DEALS_JSON=json.dumps(enriched, ensure_ascii=False),
        CATS_JSON=json.dumps(cats, ensure_ascii=False),
        SUPERS_JSON=json.dumps(supers, ensure_ascii=False),
    )
    with open("deals_viewer.html", "w", encoding="utf-8") as f:
        f.write(html)


def main():
    all_deals = []

    for naam, scraper in SCRAPERS:
        print(f"Scraping {naam}...")
        deals = scraper()
        deals = [
            d for d in deals
            if is_food(d)
            # Keep if there's a was-price OR a deal label (Lidl has no was-price)
            and (d.get("was") or d.get("deal_label"))
            # Exclude price-equal rows with no label (not a real deal)
            and (d.get("prijs") != d.get("was") or d.get("deal_label"))
        ]
        for d in deals:
            d["desc"] = normalize_category(d["desc"])
        print(f"  {len(deals)} deals gevonden")
        all_deals.extend(deals)
        time.sleep(1)

    datum = str(date.today())
    output = {
        "datum":  datum,
        "totaal": len(all_deals),
        "deals":  all_deals,
    }

    with open("deals.json", "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    generate_viewer(all_deals, datum)

    print(f"\nKlaar: {len(all_deals)} deals opgeslagen in deals.json en deals_viewer.html")


if __name__ == "__main__":
    main()
