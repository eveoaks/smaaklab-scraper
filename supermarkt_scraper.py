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
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date

import anthropic
import requests
from playwright.sync_api import sync_playwright

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

# Categorieën die tijdelijk als "Overig" worden getoond.
# Verwijder een categorie uit deze set om hem weer als eigen filterknop te tonen.
HIDDEN_CATEGORIES = {
    "Koek & snoep",
    "Snacks",
    "Frisdrank",
    "Glutenvrij",
    "Maaltijden",
}


def normalize_category(raw_cat):
    main = raw_cat.split("/")[0].strip()
    return CATEGORY_MAP.get(main)  # None = niet-eten, overgeslagen


def is_food(deal):
    return normalize_category(deal.get("desc", "")) is not None


# Trefwoordclassificatie voor supermarkten zonder categorie-API
# (Aldi, Plus, Dirk). Eerste match wint; volgorde is belangrijk.
_KEYWORD_RULES = [
    # Bier & wijn — vroeg, anders matchen "bier" ook op "bierworst" etc.
    ("bier",            "Bier & wijn"),
    ("pils",            "Bier & wijn"),
    ("radler",          "Bier & wijn"),
    ("cider",           "Bier & wijn"),
    ("wijn",            "Bier & wijn"),
    ("rosé",            "Bier & wijn"),
    ("champagne",       "Bier & wijn"),
    ("prosecco",        "Bier & wijn"),
    ("cava",            "Bier & wijn"),
    ("port ",           "Bier & wijn"),
    ("sherry",          "Bier & wijn"),
    ("whisky",          "Bier & wijn"),
    ("whiskey",         "Bier & wijn"),
    ("wodka",           "Bier & wijn"),
    ("vodka",           "Bier & wijn"),
    ("rum ",            "Bier & wijn"),
    ("gin ",            "Bier & wijn"),
    ("jenever",         "Bier & wijn"),
    ("likeur",          "Bier & wijn"),
    ("aperol",          "Bier & wijn"),
    ("tequila",         "Bier & wijn"),
    ("cognac",          "Bier & wijn"),
    ("ale ",            "Bier & wijn"),
    ("stout",           "Bier & wijn"),

    # Vegetarisch & vegan — vóór vlees/kaas
    ("vegetarisch",     "Vegetarisch & vegan"),
    ("vegan",           "Vegetarisch & vegan"),
    ("plantaardig",     "Vegetarisch & vegan"),
    ("tofu",            "Vegetarisch & vegan"),
    ("tempeh",          "Vegetarisch & vegan"),
    ("seitan",          "Vegetarisch & vegan"),
    ("quorn",           "Vegetarisch & vegan"),
    ("beyond",          "Vegetarisch & vegan"),
    ("impossible",      "Vegetarisch & vegan"),
    ("veggie",          "Vegetarisch & vegan"),
    ("sojaburger",      "Vegetarisch & vegan"),

    # Diepvries
    ("diepvries",       "Diepvries"),
    ("bevroren",        "Diepvries"),
    ("bitterballen",    "Diepvries"),
    ("frikandel",       "Diepvries"),
    ("kroket",          "Diepvries"),
    ("diepvriesgroent", "Diepvries"),

    # Vlees & vis — specifiek vóór generiek
    ("kipfilet",        "Vlees & vis"),
    ("kippenbors",      "Vlees & vis"),
    ("kippendij",       "Vlees & vis"),
    ("kipdrumstick",    "Vlees & vis"),
    ("gehakt",          "Vlees & vis"),
    ("biefstuk",        "Vlees & vis"),
    ("entrecote",       "Vlees & vis"),
    ("ribeye",          "Vlees & vis"),
    ("spareribs",       "Vlees & vis"),
    ("braadworst",      "Vlees & vis"),
    ("hamburger",       "Vlees & vis"),
    ("bacon",           "Vlees & vis"),
    ("zalm",            "Vlees & vis"),
    ("makreel",         "Vlees & vis"),
    ("haring",          "Vlees & vis"),
    ("tonijn",          "Vlees & vis"),
    ("garnalen",        "Vlees & vis"),
    ("garnaal",         "Vlees & vis"),
    ("kabeljauw",       "Vlees & vis"),
    ("tilapia",         "Vlees & vis"),
    ("forel",           "Vlees & vis"),
    ("mosselen",        "Vlees & vis"),
    ("scholle",         "Vlees & vis"),
    ("pangasius",       "Vlees & vis"),
    ("koolvis",         "Vlees & vis"),
    ("lamsvlees",       "Vlees & vis"),
    ("lamsco",          "Vlees & vis"),
    ("kalkoen",         "Vlees & vis"),
    ("rosbief",         "Vlees & vis"),
    ("varkensvlees",    "Vlees & vis"),
    ("varkensfil",      "Vlees & vis"),
    ("varkensh",        "Vlees & vis"),
    ("rundvlees",       "Vlees & vis"),
    ("speklap",         "Vlees & vis"),
    ("slavink",         "Vlees & vis"),
    ("gehaktbal",       "Vlees & vis"),
    ("schnit",          "Vlees & vis"),
    ("worst",           "Vlees & vis"),
    ("kip ",            "Vlees & vis"),
    ("vis ",            "Vlees & vis"),
    ("visfi",           "Vlees & vis"),
    ("vlees",           "Vlees & vis"),

    # Vleeswaren & kaas
    ("ham ",            "Vleeswaren & kaas"),
    ("salami",          "Vleeswaren & kaas"),
    ("chorizo",         "Vleeswaren & kaas"),
    ("pastrami",        "Vleeswaren & kaas"),
    ("rookvlees",       "Vleeswaren & kaas"),
    ("cervelaat",       "Vleeswaren & kaas"),
    ("leverworst",      "Vleeswaren & kaas"),
    ("smeerkaas",       "Vleeswaren & kaas"),
    ("kaas",            "Vleeswaren & kaas"),
    ("gouda",           "Vleeswaren & kaas"),
    ("edam",            "Vleeswaren & kaas"),
    ("cheddar",         "Vleeswaren & kaas"),
    ("brie",            "Vleeswaren & kaas"),
    ("camembert",       "Vleeswaren & kaas"),
    ("feta",            "Vleeswaren & kaas"),
    ("mozzarella",      "Vleeswaren & kaas"),
    ("parmezaan",       "Vleeswaren & kaas"),
    ("grana padano",    "Vleeswaren & kaas"),
    ("hüttenkäse",      "Vleeswaren & kaas"),
    ("ricotta",         "Vleeswaren & kaas"),
    ("mascarpone",      "Vleeswaren & kaas"),
    ("vleeswaren",      "Vleeswaren & kaas"),

    # Zuivel & eieren
    ("melk",            "Zuivel & eieren"),
    ("yoghurt",         "Zuivel & eieren"),
    ("kwark",           "Zuivel & eieren"),
    ("vla",             "Zuivel & eieren"),
    ("pudding",         "Zuivel & eieren"),
    ("slagroom",        "Zuivel & eieren"),
    ("room ",           "Zuivel & eieren"),
    ("boter",           "Zuivel & eieren"),
    ("margarine",       "Zuivel & eieren"),
    ("halvarine",       "Zuivel & eieren"),
    ("eieren",          "Zuivel & eieren"),
    ("ei ",             "Zuivel & eieren"),
    ("karnemelk",       "Zuivel & eieren"),
    ("havermelk",       "Zuivel & eieren"),
    ("sojamelk",        "Zuivel & eieren"),
    ("amandelmelk",     "Zuivel & eieren"),
    ("kokosmelk",       "Zuivel & eieren"),
    ("crème fraîche",   "Zuivel & eieren"),
    ("creme fraiche",   "Zuivel & eieren"),
    ("griekse yogh",    "Zuivel & eieren"),

    # Frisdrank
    ("cola",            "Frisdrank"),
    ("fanta",           "Frisdrank"),
    ("sprite",          "Frisdrank"),
    ("7up",             "Frisdrank"),
    ("limonade",        "Frisdrank"),
    ("sinas",           "Frisdrank"),
    ("appelsap",        "Frisdrank"),
    ("sinaasappelsap",  "Frisdrank"),
    ("vruchtensap",     "Frisdrank"),
    ("multivitamine",   "Frisdrank"),
    ("ice tea",         "Frisdrank"),
    ("ijsthee",         "Frisdrank"),
    ("energy drink",    "Frisdrank"),
    ("red bull",        "Frisdrank"),
    ("sportdrank",      "Frisdrank"),
    ("mineraalwater",   "Frisdrank"),
    ("bruiswater",      "Frisdrank"),
    ("tonic",           "Frisdrank"),
    ("ginger ale",      "Frisdrank"),
    ("ranja",           "Frisdrank"),
    ("cassis",          "Frisdrank"),
    ("sap ",            "Frisdrank"),

    # Koffie & thee
    ("koffie",          "Koffie & thee"),
    ("espresso",        "Koffie & thee"),
    ("cappuccino",      "Koffie & thee"),
    ("nespresso",       "Koffie & thee"),
    ("senseo",          "Koffie & thee"),
    ("dolce gusto",     "Koffie & thee"),
    ("thee",            "Koffie & thee"),
    ("rooibos",         "Koffie & thee"),
    ("cacao",           "Koffie & thee"),

    # Soepen & sauzen
    ("soep",            "Soepen & sauzen"),
    ("bouillon",        "Soepen & sauzen"),
    ("tomatensaus",     "Soepen & sauzen"),
    ("pastasaus",       "Soepen & sauzen"),
    ("bolognese",       "Soepen & sauzen"),
    ("carbonara",       "Soepen & sauzen"),
    ("curry",           "Soepen & sauzen"),
    ("sambal",          "Soepen & sauzen"),
    ("ketjap",          "Soepen & sauzen"),
    ("sojasaus",        "Soepen & sauzen"),
    ("mayonaise",       "Soepen & sauzen"),
    ("ketchup",         "Soepen & sauzen"),
    ("mosterd",         "Soepen & sauzen"),
    ("dressing",        "Soepen & sauzen"),
    ("marinade",        "Soepen & sauzen"),
    ("olijfolie",       "Soepen & sauzen"),
    ("zonnebloemolie",  "Soepen & sauzen"),
    ("olie",            "Soepen & sauzen"),
    ("azijn",           "Soepen & sauzen"),
    ("saus",            "Soepen & sauzen"),

    # Pasta & rijst
    ("pasta",           "Pasta & rijst"),
    ("spaghetti",       "Pasta & rijst"),
    ("tagliatelle",     "Pasta & rijst"),
    ("penne",           "Pasta & rijst"),
    ("rigatoni",        "Pasta & rijst"),
    ("fusilli",         "Pasta & rijst"),
    ("farfalle",        "Pasta & rijst"),
    ("macaroni",        "Pasta & rijst"),
    ("rijst",           "Pasta & rijst"),
    ("basmati",         "Pasta & rijst"),
    ("couscous",        "Pasta & rijst"),
    ("bulgur",          "Pasta & rijst"),
    ("quinoa",          "Pasta & rijst"),
    ("noodles",         "Pasta & rijst"),
    ("mie ",            "Pasta & rijst"),
    ("vermicelli",      "Pasta & rijst"),
    ("lasagne",         "Pasta & rijst"),

    # Maaltijden
    ("pizza",           "Maaltijden"),
    ("quiche",          "Maaltijden"),
    ("stamppot",        "Maaltijden"),
    ("hutspot",         "Maaltijden"),
    ("nasi",            "Maaltijden"),
    ("bami",            "Maaltijden"),
    ("paella",          "Maaltijden"),
    ("risotto",         "Maaltijden"),
    ("wokmaaltijd",     "Maaltijden"),
    ("ovenschotel",     "Maaltijden"),
    ("maaltijd",        "Maaltijden"),
    ("wrap",            "Maaltijden"),
    ("burrito",         "Maaltijden"),
    ("sushi",           "Maaltijden"),
    ("salade",          "Maaltijden"),

    # Groente & fruit
    ("komkommer",       "Groente & fruit"),
    ("tomaat",          "Groente & fruit"),
    ("tomaten",         "Groente & fruit"),
    ("paprika",         "Groente & fruit"),
    ("sla ",            "Groente & fruit"),
    ("andijvie",        "Groente & fruit"),
    ("spinazie",        "Groente & fruit"),
    ("broccoli",        "Groente & fruit"),
    ("bloemkool",       "Groente & fruit"),
    ("courgette",       "Groente & fruit"),
    ("prei",            "Groente & fruit"),
    ("ui ",             "Groente & fruit"),
    ("uien",            "Groente & fruit"),
    ("knoflook",        "Groente & fruit"),
    ("wortel",          "Groente & fruit"),
    ("aardappel",       "Groente & fruit"),
    ("champignon",      "Groente & fruit"),
    ("avocado",         "Groente & fruit"),
    ("mango",           "Groente & fruit"),
    ("appel",           "Groente & fruit"),
    ("peer ",           "Groente & fruit"),
    ("banaan",          "Groente & fruit"),
    ("aardbei",         "Groente & fruit"),
    ("druiven",         "Groente & fruit"),
    ("citroen",         "Groente & fruit"),
    ("sinaasappel",     "Groente & fruit"),
    ("kiwi",            "Groente & fruit"),
    ("meloen",          "Groente & fruit"),
    ("ananas",          "Groente & fruit"),
    ("perzik",          "Groente & fruit"),
    ("pruim",           "Groente & fruit"),
    ("kersen",          "Groente & fruit"),
    ("frambozen",       "Groente & fruit"),
    ("bosbessen",       "Groente & fruit"),
    ("bessen",          "Groente & fruit"),
    ("sperziebonen",    "Groente & fruit"),
    ("erwten",          "Groente & fruit"),
    ("maïs",            "Groente & fruit"),
    ("mais",            "Groente & fruit"),
    ("asperge",         "Groente & fruit"),
    ("rode kool",       "Groente & fruit"),
    ("witte kool",      "Groente & fruit"),
    ("ijsbergsla",      "Groente & fruit"),
    ("rucola",          "Groente & fruit"),
    ("veldsla",         "Groente & fruit"),
    ("radijs",          "Groente & fruit"),
    ("biet",            "Groente & fruit"),
    ("zoete aardappel", "Groente & fruit"),
    ("peterselie",      "Groente & fruit"),
    ("basilicum",       "Groente & fruit"),
    ("groente",         "Groente & fruit"),
    ("fruit",           "Groente & fruit"),

    # Brood & gebak
    ("brood",           "Brood & gebak"),
    ("broodje",         "Brood & gebak"),
    ("stokbrood",       "Brood & gebak"),
    ("ciabatta",        "Brood & gebak"),
    ("baguette",        "Brood & gebak"),
    ("croissant",       "Brood & gebak"),
    ("beschuit",        "Brood & gebak"),
    ("crackers",        "Brood & gebak"),
    ("toast",           "Brood & gebak"),
    ("ontbijtkoek",     "Brood & gebak"),
    ("cake",            "Brood & gebak"),
    ("taart",           "Brood & gebak"),
    ("muffin",          "Brood & gebak"),
    ("wafels",          "Brood & gebak"),
    ("pannenkoek",      "Brood & gebak"),
    ("pistolet",        "Brood & gebak"),
    ("boterkoek",       "Brood & gebak"),
    ("appelflap",       "Brood & gebak"),
    ("gevulde koek",    "Brood & gebak"),
    ("speculaas",       "Brood & gebak"),
    ("stroopwafel",     "Brood & gebak"),
    ("gebak",           "Brood & gebak"),

    # Koek & snoep
    ("snoep",           "Koek & snoep"),
    ("drop",            "Koek & snoep"),
    ("kauwgom",         "Koek & snoep"),
    ("lolly",           "Koek & snoep"),
    ("chocolade",       "Koek & snoep"),
    ("chocola",         "Koek & snoep"),
    ("pralines",        "Koek & snoep"),
    ("bonbons",         "Koek & snoep"),
    ("koekjes",         "Koek & snoep"),
    ("biscuit",         "Koek & snoep"),
    ("pepernoten",      "Koek & snoep"),
    ("marshmallow",     "Koek & snoep"),
    ("koek",            "Koek & snoep"),

    # Snacks
    ("chips",           "Snacks"),
    ("pinda",           "Snacks"),
    ("nootjes",         "Snacks"),
    ("cashew",          "Snacks"),
    ("amandelen",       "Snacks"),
    ("popcorn",         "Snacks"),
    ("pretzels",        "Snacks"),
    ("tortilla",        "Snacks"),
    ("nachos",          "Snacks"),
    ("rijstwafels",     "Snacks"),
    ("notenm",          "Snacks"),
    ("snack",           "Snacks"),

    # Ontbijt & beleg
    ("pindakaas",       "Ontbijt & beleg"),
    ("hagelslag",       "Ontbijt & beleg"),
    ("jam",             "Ontbijt & beleg"),
    ("confituur",       "Ontbijt & beleg"),
    ("honing",          "Ontbijt & beleg"),
    ("nutella",         "Ontbijt & beleg"),
    ("choco",           "Ontbijt & beleg"),
    ("appelstroop",     "Ontbijt & beleg"),
    ("hummus",          "Ontbijt & beleg"),
    ("müsli",           "Ontbijt & beleg"),
    ("muesli",          "Ontbijt & beleg"),
    ("granola",         "Ontbijt & beleg"),
    ("cornflakes",      "Ontbijt & beleg"),
    ("havermout",       "Ontbijt & beleg"),
    ("vlokken",         "Ontbijt & beleg"),
    ("beleg",           "Ontbijt & beleg"),
    ("ontbijt",         "Ontbijt & beleg"),

    # Extra bier-merken (niet gevonden via "bier")
    ("heineken",        "Bier & wijn"),
    ("grolsch",         "Bier & wijn"),
    ("amstel",          "Bier & wijn"),
    ("bavaria",         "Bier & wijn"),
    ("jupiler",         "Bier & wijn"),
    ("hertog jan",      "Bier & wijn"),
    ("brand bier",      "Bier & wijn"),
    ("texels",          "Bier & wijn"),
    ("seltzer",         "Bier & wijn"),
    ("riesling",        "Bier & wijn"),
    ("rioja",           "Bier & wijn"),
    ("chardonnay",      "Bier & wijn"),
    ("sauvignon",       "Bier & wijn"),
    ("merlot",          "Bier & wijn"),
    ("brunello",        "Bier & wijn"),
    ("berenburg",       "Bier & wijn"),

    # Extra frisdrank-merken
    ("spa ",            "Frisdrank"),
    ("crystal clear",   "Frisdrank"),
    ("fuze",            "Frisdrank"),
    ("karvan",          "Frisdrank"),
    ("dubbelfrisss",    "Frisdrank"),
    ("maaza",           "Frisdrank"),
    ("taksi",           "Frisdrank"),
    ("oasis",           "Frisdrank"),
    ("dubbeldrank",     "Frisdrank"),
    ("stülz",           "Frisdrank"),

    # Extra koffie-merken
    ("douwe egberts",   "Koffie & thee"),
    ("nescafé",         "Koffie & thee"),
    ("illy",            "Koffie & thee"),
    ("ice coffee",      "Koffie & thee"),

    # Diepvries — ijs en friet
    (" ijs",            "Diepvries"),
    ("ijs ",            "Diepvries"),
    ("ijsbeker",        "Diepvries"),
    ("friet",           "Diepvries"),
    ("frites",          "Diepvries"),
    ("aviko",           "Diepvries"),

    # Vlees & vis — extra
    ("chipolata",       "Vlees & vis"),
    ("gyros",           "Vlees & vis"),
    ("scharrelkip",     "Vlees & vis"),
    ("kip",             "Vlees & vis"),
    ("knaks",           "Vlees & vis"),
    ("kipstuck",        "Vlees & vis"),
    ("kipreep",         "Vlees & vis"),
    ("visburger",       "Vlees & vis"),
    ("ham",             "Vleeswaren & kaas"),  # na "hamburger" in lijst

    # Vleeswaren & kaas — extra
    ("emmentaler",      "Vleeswaren & kaas"),
    ("emmental",        "Vleeswaren & kaas"),
    ("fuet",            "Vleeswaren & kaas"),
    ("ibérico",         "Vleeswaren & kaas"),
    ("iberico",         "Vleeswaren & kaas"),
    ("prosciutto",      "Vleeswaren & kaas"),
    ("coppa",           "Vleeswaren & kaas"),
    ("compaxo",         "Vleeswaren & kaas"),
    ("antipasti",       "Vleeswaren & kaas"),
    ("tapas",           "Vleeswaren & kaas"),

    # Zuivel — extra
    ("skyr",            "Zuivel & eieren"),
    ("zuivel",          "Zuivel & eieren"),
    ("müllermilk",      "Zuivel & eieren"),
    ("mullermilk",      "Zuivel & eieren"),
    ("oatly",           "Zuivel & eieren"),
    ("haverdrink",      "Zuivel & eieren"),

    # Groente & fruit — extra
    ("mandarijn",       "Groente & fruit"),
    ("snijbonen",       "Groente & fruit"),
    ("spruiten",        "Groente & fruit"),
    ("witlof",          "Groente & fruit"),
    ("pastinaak",       "Groente & fruit"),
    ("olijven",         "Groente & fruit"),
    ("olijf",           "Groente & fruit"),
    ("vijg",            "Groente & fruit"),
    ("dadels",          "Groente & fruit"),
    ("pijnboompit",     "Groente & fruit"),
    ("shiitake",        "Groente & fruit"),
    ("bananen",         "Groente & fruit"),

    # Brood & gebak — extra
    ("donut",           "Brood & gebak"),
    ("focaccia",        "Brood & gebak"),
    ("eclair",          "Brood & gebak"),
    ("bollen",          "Brood & gebak"),
    ("desem",           "Brood & gebak"),
    ("pistolets",       "Brood & gebak"),

    # Koek & snoep — merken & extra
    ("haribo",          "Koek & snoep"),
    ("mentos",          "Koek & snoep"),
    ("skittles",        "Koek & snoep"),
    ("milka",           "Koek & snoep"),
    ("kitkat",          "Koek & snoep"),
    ("twix",            "Koek & snoep"),
    ("snickers",        "Koek & snoep"),
    ("mars ",           "Koek & snoep"),
    ("bounty",          "Koek & snoep"),
    ("kinder",          "Koek & snoep"),
    ("bueno",           "Koek & snoep"),
    ("oreo",            "Koek & snoep"),
    ("daim",            "Koek & snoep"),
    ("pepermunt",       "Koek & snoep"),
    ("smint",           "Koek & snoep"),
    ("wilhelmina",      "Koek & snoep"),
    ("red band",        "Koek & snoep"),
    ("venco",           "Koek & snoep"),
    ("jelly bean",      "Koek & snoep"),
    ("stimorol",        "Koek & snoep"),
    ("bubblicious",     "Koek & snoep"),
    ("wafers",          "Koek & snoep"),
    ("balconi",         "Koek & snoep"),
    ("nestlé",          "Koek & snoep"),
    ("nestle",          "Koek & snoep"),
    ("stroop",          "Koek & snoep"),

    # Snacks — extra
    ("pringles",        "Snacks"),
    ("doritos",         "Snacks"),
    ("chio",            "Snacks"),
    ("studentenhaver",  "Snacks"),
    ("borrelschotel",   "Snacks"),
    ("borrel",          "Snacks"),
    ("protein reep",    "Snacks"),
    ("proteinereep",    "Snacks"),
    ("noten",           "Snacks"),

    # Maaltijden — extra
    ("pizza",           "Maaltijden"),
    ("wagner",          "Maaltijden"),
    ("pokébowl",        "Maaltijden"),
    ("pokebowl",        "Maaltijden"),
    ("lahmacun",        "Maaltijden"),
    ("wereldgerecht",   "Maaltijden"),
    ("roerbak",         "Maaltijden"),

    # Pasta & rijst — extra
    ("noedels",         "Pasta & rijst"),
    ("ramen",           "Pasta & rijst"),
    ("nissin",          "Pasta & rijst"),

    # Ontbijt & beleg — extra
    ("snelle jelle",    "Ontbijt & beleg"),
    ("schenkstroop",    "Ontbijt & beleg"),

    # Groente & fruit — extra
    ("peren",           "Groente & fruit"),
    ("verspakket",      "Groente & fruit"),
    ("mandarijnen",     "Groente & fruit"),

    # Vlees & vis — extra
    ("hachee",          "Vlees & vis"),
    ("riblappen",       "Vlees & vis"),
    ("runderhach",      "Vlees & vis"),

    # Brood & gebak — extra
    ("korenlanders",    "Brood & gebak"),
    ("volkoren",        "Brood & gebak"),
    ("meergranen",      "Brood & gebak"),
    ("spelt",           "Brood & gebak"),
    ("stolletjes",      "Brood & gebak"),
    ("grissini",        "Brood & gebak"),
    ("pistolet",        "Brood & gebak"),

    # Soepen & sauzen — extra
    ("aioli",           "Soepen & sauzen"),
    ("sriracha",        "Soepen & sauzen"),
    ("specerij",        "Soepen & sauzen"),

    # Vleeswaren & kaas — extra
    ("fourme",          "Vleeswaren & kaas"),
    ("ambert",          "Vleeswaren & kaas"),

    # Diepvries — extra
    ("tartufo",         "Diepvries"),
]


def classify_by_name(naam):
    """Categoriseer een productnaam via trefwoorden. Geeft 'Overig' als er geen match is."""
    naam_lower = naam.lower()
    for keyword, category in _KEYWORD_RULES:
        if keyword in naam_lower:
            return category
    return "Overig"


# ──────────────────────────────────────────────
# Albert Heijn — native GraphQL API
# ──────────────────────────────────────────────

AH_TOKEN_URL    = "https://api.ah.nl/mobile-auth/v1/auth/token/anonymous"
AH_GRAPHQL_URL  = "https://api.ah.nl/graphql"
AH_PRODUCT_URL  = "https://api.ah.nl/mobile-services/product/detail/v4/fir/{}"

# Product-level fields (title/category/imagePack) now return null in GraphQL.
# We fetch only IDs + prices via GraphQL, then resolve details via REST.
AH_QUERY = """
{
  bonusPromotions {
    id
    title
    promotionType
    products {
      id
      price {
        now { amount }
        was { amount }
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


def _ah_fetch_product_detail(pid, auth_headers):
    """Fetch title, category, image, and bonus info for a single product ID."""
    try:
        r = requests.get(
            AH_PRODUCT_URL.format(pid),
            headers=auth_headers,
            timeout=10,
        )
        if r.ok:
            card = r.json().get("productCard") or {}
            images = card.get("images") or []
            img = next((i["url"] for i in images if i.get("width", 0) >= 200), "")
            discount_labels = card.get("discountLabels") or []
            bonus_mechanism = card.get("bonusMechanism") or ""
            if not bonus_mechanism and discount_labels:
                bonus_mechanism = discount_labels[0].get("defaultDescription", "")
            return {
                "title":         card.get("title", ""),
                "category":      card.get("mainCategory", ""),
                "img":           img,
                "bonus_mechanism": bonus_mechanism,
                "price_before":  card.get("priceBeforeBonus"),
            }
    except Exception:
        pass
    return {"title": "", "category": "", "img": "", "bonus_mechanism": "", "price_before": None}


def scrape_ah():
    try:
        token = ah_get_token()
    except Exception as e:
        print(f"  FOUT Albert Heijn (token): {e}")
        return []

    gql_headers = {
        "Authorization": f"Bearer {token}",
        "x-application": "AHWEBSHOP",
        "Content-Type": "application/json",
    }
    rest_headers = {
        "Authorization": f"Bearer {token}",
        "x-application": "AHWEBSHOP",
    }

    try:
        r = requests.post(
            AH_GRAPHQL_URL,
            json={"query": AH_QUERY},
            headers=gql_headers,
            timeout=TIMEOUT,
        )
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        print(f"  FOUT Albert Heijn (graphql): {e}")
        return []

    gql_data = data.get("data") or {}
    promotions = gql_data.get("bonusPromotions", [])

    national = [
        p for p in promotions
        if p.get("promotionType") == "NATIONAL" and p.get("products")
    ]

    # Collect unique product IDs (first product per promo is enough for detail)
    unique_pids = list({p["products"][0]["id"] for p in national})

    # Fetch product details concurrently (20 workers ≈ 5-10s for ~300 products)
    detail_cache = {}
    with ThreadPoolExecutor(max_workers=20) as executor:
        fut_map = {
            executor.submit(_ah_fetch_product_detail, pid, rest_headers): pid
            for pid in unique_pids
        }
        for fut in as_completed(fut_map):
            detail_cache[fut_map[fut]] = fut.result()

    results = []
    seen = set()

    for promo in national:
        pid        = promo["products"][0]["id"]
        detail     = detail_cache.get(pid, {})
        promo_title = (promo.get("title") or "").strip()

        naam = detail.get("title") or promo_title
        if not naam:
            continue
        key = naam.lower()
        if key in seen:
            continue
        seen.add(key)

        prod_price = promo["products"][0].get("price") or {}
        now_amt  = (prod_price.get("now")  or {}).get("amount")
        was_amt  = (prod_price.get("was")  or {}).get("amount")

        # Fallback: use priceBeforeBonus from REST detail as was price when GraphQL was is None
        if was_amt is None and detail.get("price_before") and detail["price_before"] != now_amt:
            was_amt = detail["price_before"]

        # deal_label from REST detail (bonusMechanism / discountLabels)
        deal_label = detail.get("bonus_mechanism") or None

        # Compute percentage discount label when we have both prices
        if now_amt and was_amt and was_amt > now_amt and not deal_label:
            pct = round((1 - now_amt / was_amt) * 100)
            deal_label = f"{pct}% korting"

        effectief_prijs = None

        # Fallback now_amt from REST detail when GraphQL price.now is missing
        if now_amt is None and detail.get("price_before"):
            now_amt = detail["price_before"]

        # Skip deals with no usable price and no deal info
        if not now_amt and not deal_label:
            continue
        if not was_amt and not deal_label:
            continue

        results.append({
            "supermarkt":    "Albert Heijn",
            "naam":          naam,
            "desc":          detail.get("category", ""),
            "prijs":         str(now_amt)  if now_amt  is not None else None,
            "was":           str(was_amt)  if was_amt  is not None else None,
            "deal_label":    deal_label,
            "effectief":     str(effectief_prijs) if effectief_prijs is not None else None,
            "img":           detail.get("img", ""),
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
# Lidl — Leaflets API + Claude Vision (geen Playwright)
# ──────────────────────────────────────────────

LIDL_API_URL = "https://endpoints.leaflets.schwarz/v4/flyer?flyer_identifier={slug}"

LIDL_VISION_PROMPT = (
    "Dit is een pagina uit de Lidl weekfolder. "
    "Extraheer ALLEEN voedselproducten (geen huishoudartikelen, drogisterij, kleding, elektronica, schoonmaakmiddelen, fietsen, gereedschap).\n\n"
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



def _parse_claude_products(text):
    """Parse Claude JSON response, strip markdown code fences and trailing text."""
    text = text.strip()
    text = re.sub(r'^```(?:json)?\s*', '', text)
    text = re.sub(r'\s*```$', '', text)
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

    iso = date.today().isocalendar()
    slug = f"hah-wk{iso[1]}-{iso[0]}"
    folder_url = f"https://www.lidl.nl/l/folders/{slug}/view/flyer/page/1"
    print(f"  Lidl folder: {slug}")

    # Haal alle pagina's op via de leaflets API
    api_resp = requests.get(LIDL_API_URL.format(slug=slug), timeout=TIMEOUT)
    api_resp.raise_for_status()
    flyer_data = api_resp.json()["flyer"]
    pages = flyer_data["pages"]
    print(f"  {len(pages)} pagina's gevonden in folder")

    # Verzamel non-food productIds uit de gestructureerde API (fietsen, tools, kleding)
    nonfood_ids = {
        p.get("productId")
        for p in flyer_data.get("products", {}).values()
        if p.get("productId")
    }

    client  = anthropic.Anthropic(api_key=api_key)
    results = []
    seen    = set()

    for page in pages:
        page_num  = page["number"]
        alt_text  = page.get("altText", "")
        img_url   = page.get("image") or page.get("zoom")

        if not img_url:
            continue

        # Sla pagina over als ALLE links non-food zijn
        links = page.get("links", [])
        if links:
            link_ids = {
                str(l.get("productDetails", {}).get("productId", ""))
                for l in links if l.get("displayType") == "product"
            }
            if link_ids and link_ids.issubset(nonfood_ids):
                print(f"  Pagina {page_num}: overgeslagen (puur non-food)")
                continue

        print(f"  Pagina {page_num}: verwerken...")

        try:
            img_r = requests.get(img_url, timeout=30)
            img_r.raise_for_status()
            img_b64 = base64.standard_b64encode(img_r.content).decode("utf-8")
            content_type = img_r.headers.get("Content-Type", "image/jpeg").split(";")[0].strip()
            if content_type not in ("image/jpeg", "image/png", "image/webp", "image/gif"):
                content_type = "image/jpeg"

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
                                "media_type": content_type,
                                "data": img_b64,
                            },
                        },
                        {"type": "text", "text": LIDL_VISION_PROMPT},
                    ],
                }],
            )

            products = _parse_claude_products(response.content[0].text)
            page_count = 0

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
                    "img":        img_url,
                    "url":        folder_url,
                })
                page_count += 1

            print(f"    → {page_count} food producten")
            time.sleep(0.5)

        except json.JSONDecodeError as e:
            print(f"  Pagina {page_num}: JSON parse fout – {e}")
        except Exception as e:
            print(f"  Pagina {page_num}: {e}")

    return results


# Aldi — Next.js embedded JSON
# ──────────────────────────────────────────────

ALDI_URL = "https://www.aldi.nl/aanbiedingen-deze-week.html"


def scrape_aldi():
    r = requests.get(ALDI_URL, timeout=TIMEOUT, headers={
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
    })
    r.raise_for_status()

    match = re.search(r'id="__NEXT_DATA__"[^>]*>(.*?)</script>', r.text, re.S)
    if not match:
        print("  FOUT Aldi: __NEXT_DATA__ niet gevonden")
        return []

    data     = json.loads(match.group(1))
    api_data = json.loads(data["props"]["pageProps"]["apiData"])

    products_map = {}
    for entry in api_data:
        if isinstance(entry, list) and len(entry) == 2:
            key, val = entry
            if key == "OFFER_GET" and "res" in val:
                products_map.update(val["res"].get("algoliaDataMap", {}))

    results = []
    for p in products_map.values():
        naam = (p.get("name") or "").strip()
        if not naam:
            continue

        cp        = p.get("currentPrice", {})
        prijs_val = cp.get("priceValue")
        if prijs_val is None:
            continue
        prijs_str = str(prijs_val).replace(",", ".")

        deal_label = (cp.get("priceTagLabels") or {}).get("promoText1", "").strip() or "Weekaanbieding"

        img_url = next(
            (a["url"] for a in p.get("assets", []) if a.get("type") == "primary"),
            None,
        )
        slug    = p.get("productSlug", "")
        url     = f"https://www.aldi.nl/producten/aanbiedingen/{slug}.html" if slug else ALDI_URL

        results.append({
            "supermarkt": "Aldi",
            "naam":       naam,
            "desc":       "Weekaanbieding",
            "prijs":      prijs_str,
            "was":        None,
            "deal_label": deal_label,
            "effectief":  None,
            "img":        img_url,
            "url":        url,
        })

    return results


# ──────────────────────────────────────────────
# Dirk — Nuxt __NUXT_DATA__ embedded JSON
# ──────────────────────────────────────────────

DIRK_URL        = "https://www.dirk.nl/aanbiedingen"
DIRK_IMG_BASE   = "https://web-fileserver.dirk.nl/artikelen/"
DIRK_HEADERS    = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
DIRK_NON_FOOD   = {"Huishoud & huisdieren", "Kind & drogisterij"}


def scrape_dirk():
    try:
        r = requests.get(DIRK_URL, timeout=TIMEOUT, headers=DIRK_HEADERS)
        r.raise_for_status()
    except Exception as e:
        print(f"  FOUT Dirk (request): {e}")
        return []

    m = re.search(r'<script[^>]*id="__NUXT_DATA__"[^>]*>(.*?)</script>', r.text, re.S)
    if not m:
        print("  FOUT Dirk: __NUXT_DATA__ niet gevonden")
        return []

    try:
        data = json.loads(m.group(1))
    except Exception as e:
        print(f"  FOUT Dirk (JSON parse): {e}")
        return []

    def resolve(v):
        return data[v] if isinstance(v, int) and v < len(data) else v

    # Alle offer-objecten hebben 'offerPrice' als key
    results = []
    seen    = set()

    for item in data:
        if not isinstance(item, dict) or "offerPrice" not in item:
            continue

        naam = resolve(item.get("headerText", ""))
        if not isinstance(naam, str) or not naam.strip():
            continue
        naam = naam.strip()

        key = naam.lower()
        if key in seen:
            continue

        prijs = resolve(item.get("offerPrice"))
        try:
            prijs = float(prijs)
            if prijs <= 0:
                continue
        except (TypeError, ValueError):
            continue

        # Haal department op via products → productInformation → department
        dept = None
        prods = resolve(item.get("products"))
        if isinstance(prods, list) and prods:
            prod = data[prods[0]] if isinstance(prods[0], int) else prods[0]
            if isinstance(prod, dict):
                pinfo = resolve(prod.get("productInformation"))
                if isinstance(pinfo, dict):
                    dept = resolve(resolve(pinfo.get("department")))
        if isinstance(dept, str) and dept in DIRK_NON_FOOD:
            continue

        was = resolve(item.get("normalPrice"))
        try:
            was = float(was)
            was = str(was) if was > 0 and was != prijs else None
        except (TypeError, ValueError):
            was = None

        packaging = resolve(item.get("packaging") or "")
        deal_label = resolve(item.get("textPriceSign") or "")
        if isinstance(deal_label, str):
            deal_label = deal_label.replace("_actie", "").strip().title() or "Weekaanbieding"
        else:
            deal_label = "Weekaanbieding"

        if packaging and isinstance(packaging, str):
            naam = f"{naam} — {packaging.strip()}"

        img_path = resolve(item.get("image") or "")
        img = f"{DIRK_IMG_BASE}{img_path}" if img_path and isinstance(img_path, str) else ""

        seen.add(key)
        results.append({
            "supermarkt": "Dirk",
            "naam":       naam,
            "desc":       "Weekaanbieding",
            "prijs":      str(prijs),
            "was":        was,
            "deal_label": deal_label,
            "effectief":  None,
            "img":        img,
            "url":        DIRK_URL,
        })

    return results


# ──────────────────────────────────────────────
# Plus — Playwright + network interception
# ──────────────────────────────────────────────

PLUS_URL      = "https://www.plus.nl/aanbiedingen"
PLUS_ALL_URL  = "https://www.plus.nl/aanbiedingen/alle-aanbiedingen"

# Food-subcategorieën als safety net (geen huishouden/drogisterij)
PLUS_CAT_URLS = [
    "https://www.plus.nl/aanbiedingen/groente-fruit",
    "https://www.plus.nl/aanbiedingen/vlees-vis-vega",
    "https://www.plus.nl/aanbiedingen/zuivel-eieren",
    "https://www.plus.nl/aanbiedingen/brood-gebak",
    "https://www.plus.nl/aanbiedingen/diepvries",
    "https://www.plus.nl/aanbiedingen/kaas-vleeswaren",
    "https://www.plus.nl/aanbiedingen/pasta-rijst",
    "https://www.plus.nl/aanbiedingen/frisdrank-sappen",
    "https://www.plus.nl/aanbiedingen/koffie-thee",
    "https://www.plus.nl/aanbiedingen/koek-snoep",
    "https://www.plus.nl/aanbiedingen/ontbijt-beleg",
    "https://www.plus.nl/aanbiedingen/maaltijden",
    "https://www.plus.nl/aanbiedingen/dranken",
    "https://www.plus.nl/aanbiedingen/snacks",
]


def _plus_item_toevoegen(naam_raw, brand_raw, prijs_raw, was_raw, label_raw, img, slug, results, seen):
    """Voeg één Plus product toe aan results als het geldig is."""
    brand = (brand_raw or "").replace("Alle ", "").replace("alle ", "").strip()
    naam  = (naam_raw or "").strip()
    if brand and brand.lower() not in naam.lower():
        naam = f"{brand} {naam}".strip()
    if not naam or len(naam) < 3:
        return

    key = naam.lower()
    if key in seen:
        return

    try:
        prijs = float(str(prijs_raw).replace(",", "."))
        if prijs <= 0:
            return
    except (ValueError, TypeError):
        return

    try:
        was = float(str(was_raw).replace(",", "."))
        was = str(was) if was > 0 else None
    except (ValueError, TypeError):
        was = None

    deal_label = (label_raw or "").strip().title() or "Weekaanbieding"
    url = f"https://www.plus.nl/aanbiedingen/{slug}" if slug else PLUS_URL

    seen.add(key)
    results.append({
        "supermarkt": "Plus",
        "naam":       naam,
        "desc":       "Weekaanbieding",
        "prijs":      str(prijs),
        "was":        was,
        "deal_label": deal_label,
        "effectief":  None,
        "img":        img or "",
        "url":        url,
    })


def _parse_plus_network(data, results, seen):
    """Parseer OutSystems PromotionOfferList JSON naar deals."""
    sections = data.get("data", {}).get("PromotionOfferList", {}).get("List", [])
    for section in sections:
        if not isinstance(section, dict):
            continue

        # Bron 1: ProductPromotionBanner.ProductPromotionTiles
        banner = section.get("ProductPromotionBanner", {})
        tiles  = banner.get("ProductPromotionTiles", {})
        if isinstance(tiles, dict):
            tiles = tiles.get("List", [])
        for tile in (tiles or []):
            if not isinstance(tile, dict) or tile.get("IsFreeDeliveryOffer"):
                continue
            tile_naam = tile.get("ProductName") or ""
            if re.match(r'^\d', tile_naam) or not tile_naam:
                brand = (tile.get("Brand") or "").replace("Alle ", "").replace("alle ", "").strip()
                example = re.sub(r'^[Bb]ijv\.\s*', '', tile.get("Example") or "").split(",")[0].strip()
                tile_naam = brand or example or tile.get("Variant") or tile_naam
            if re.match(r'^\d[\d,.]* (VOOR|PER)', tile_naam, re.IGNORECASE):
                continue
            _plus_item_toevoegen(
                naam_raw  = tile_naam,
                brand_raw = tile.get("Brand"),
                prijs_raw = tile.get("NewPrice"),
                was_raw   = tile.get("PriceOriginal_Highest"),
                label_raw = tile.get("DisplayInfo_Label"),
                img       = tile.get("ImageURL", ""),
                slug      = tile.get("Slug", ""),
                results=results, seen=seen,
            )

        # Bron 2: Category.Offers
        offers = section.get("Category", {}).get("Offers", {})
        if isinstance(offers, dict):
            offers = offers.get("List", [])
        for offer in (offers or []):
            if not isinstance(offer, dict) or offer.get("IsFreeDeliveryOffer"):
                continue
            try:
                prijs = float(str(offer.get("NewPrice") or "0").replace(",", "."))
                if prijs <= 0:
                    continue
            except (ValueError, TypeError):
                continue
            naam_offer = offer.get("Name") or ""
            if re.match(r'^\d', naam_offer) or not naam_offer:
                brand = (offer.get("Brand") or "").replace("Alle ", "").replace("alle ", "").strip()
                example = re.sub(r'^[Bb]ijv\.\s*', '', offer.get("Example") or "").split(",")[0].strip()
                naam_offer = brand or example or offer.get("Variant") or naam_offer
            if re.match(r'^\d[\d,.]* (VOOR|PER)', naam_offer, re.IGNORECASE):
                continue
            _plus_item_toevoegen(
                naam_raw  = naam_offer,
                brand_raw = offer.get("Brand"),
                prijs_raw = offer.get("NewPrice"),
                was_raw   = offer.get("PriceOriginal_Highest"),
                label_raw = offer.get("DisplayInfo_Label"),
                img       = offer.get("ImageURL", ""),
                slug      = offer.get("Slug", ""),
                results=results, seen=seen,
            )


def _parse_plus_dom(page):
    """DOM-fallback: extraheer deals uit rendered Plus aanbiedingen pagina.
    Geeft (results, was_map) terug waarbij was_map {naam_lower: was_str} is
    voor alle kaarten waar een hogere was-prijs gevonden wordt."""
    results = []
    seen = set()
    was_map = {}  # naam_lower → was_prijs (voor update van network results)

    cards = page.query_selector_all(".plp-results-list > a, [data-block*='OfferItem'] a")
    print(f"  DOM: {len(cards)} kaarten gevonden")

    for card in cards:
        try:
            full_text = card.inner_text().strip()
            if not full_text:
                continue

            lines = [l.strip() for l in full_text.splitlines() if l.strip()]

            # Combineer gesplitste prijzen: "2." + "49" → "2.49"
            combined = []
            i = 0
            while i < len(lines):
                if re.match(r'^\d+\.$', lines[i]) and i + 1 < len(lines) and re.match(r'^\d{2}$', lines[i + 1]):
                    combined.append(lines[i] + lines[i + 1])
                    i += 2
                else:
                    combined.append(lines[i])
                    i += 1
            lines = combined

            # Prijs: zoek eerste regel die een getal bevat met optionele € en komma/punt
            prijs = None
            prijs_idx = None
            for idx, line in enumerate(lines):
                m = re.search(r"(\d+)[,.](\d{2})", line)
                if m:
                    prijs = float(f"{m.group(1)}.{m.group(2)}")
                    prijs_idx = idx
                    break

            if prijs is None:
                continue

            # Was-prijs: zoek eerste prijs na prijs_idx die hoger is dan huidige prijs
            was = None
            for line in lines[prijs_idx + 1:]:
                m2 = re.search(r"(\d+)[,.](\d{2})", line)
                if m2:
                    was_val = float(f"{m2.group(1)}.{m2.group(2)}")
                    if was_val > prijs:
                        was = str(was_val)
                        break

            # Naam: langste regel zonder prijs of aanbiedingslabel (alle regels)
            naam_candidates = [
                l for l in lines
                if not re.search(r"\d+[,.]\d{2}", l)
                and not re.match(r'^\d[\d,.]* (VOOR|PER)', l, re.IGNORECASE)
                and len(l) >= 3
            ]
            if not naam_candidates:
                continue
            naam = max(naam_candidates, key=len).strip()
            if not naam or len(naam) < 3:
                continue

            key = naam.lower()

            # Sla was-prijs op zodat network-results bijgewerkt kunnen worden
            if was:
                was_map[key] = was

            if key in seen:
                continue

            # Deal label: zoek regel met bekende aanbiedingstekst
            deal_label = "Weekaanbieding"
            for line in lines:
                if re.search(r"(\d\+\d|gratis|korting|voor\s+€?\d|halve\s+prijs|\d[\d,.]*\s+(voor|per)\b)", line, re.I):
                    deal_label = line
                    break

            img_el = card.query_selector("img")
            img = img_el.get_attribute("src") if img_el else ""

            seen.add(key)
            results.append({
                "supermarkt": "Plus",
                "naam":       naam,
                "desc":       "Weekaanbieding",
                "prijs":      str(prijs),
                "was":        was,
                "deal_label": deal_label,
                "effectief":  None,
                "img":        img or "",
                "url":        PLUS_URL,
            })
        except Exception:
            continue

    return results, was_map


def scrape_plus():
    results = []
    seen = set()
    captured_responses = []

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                locale="nl-NL",
                viewport={"width": 1280, "height": 900},
            )
            page = context.new_page()

            def on_response(response):
                if response.status != 200:
                    return
                if "json" not in response.headers.get("content-type", ""):
                    return
                if not any(d in response.url for d in ["plus.nl", "screenservices"]):
                    return
                if any(s in response.url for s in ["analytics", "tracking", "piwik", "gtm", "hotjar"]):
                    return
                captured_responses.append(response)

            page.on("response", on_response)
            page.goto(PLUS_URL, timeout=45000, wait_until="domcontentloaded")
            page.wait_for_timeout(8000)

            # Scroll + klik "bekijk meer" knoppen totdat pagina stabiel is
            prev_count = 0
            for _ in range(30):
                # Scroll geleidelijk
                page.evaluate("window.scrollBy(0, 600)")
                page.wait_for_timeout(300)
                page.evaluate("window.scrollBy(0, 600)")
                page.wait_for_timeout(300)
                page.evaluate("window.scrollBy(0, 600)")
                page.wait_for_timeout(800)

                # Klik alle "bekijk meer" / "toon meer" knoppen die zichtbaar zijn
                for btn in page.query_selector_all("button, a"):
                    try:
                        txt = (btn.inner_text() or "").strip().lower()
                        if any(t in txt for t in ["bekijk meer", "toon meer", "meer aanbiedingen", "bekijk alle"]):
                            if btn.is_visible():
                                btn.click()
                                page.wait_for_timeout(1500)
                    except Exception:
                        pass

                count = len(page.query_selector_all(".plp-results-list > a"))
                if count == prev_count:
                    break
                prev_count = count

            print(f"  Na scrollen: {prev_count} kaarten zichtbaar")

            # Navigeer via SPA naar subcategoriepagina's voor extra producten
            PLUS_SUBCATS = [
                "groente-fruit",
                "vlees-vis-vega",
                "zuivel-eieren",
                "brood-gebak",
                "diepvries",
                "kaas-vleeswaren",
                "pasta-rijst",
                "koffie-thee",
                "ontbijt-beleg",
            ]
            for subcat in PLUS_SUBCATS:
                try:
                    # SPA-navigatie via history API ipv full reload
                    page.evaluate(f"window.history.pushState({{}},'','/aanbiedingen/{subcat}')")
                    page.wait_for_timeout(500)
                    page.evaluate("window.dispatchEvent(new PopStateEvent('popstate'))")
                    page.wait_for_timeout(3000)
                    for _ in range(3):
                        page.evaluate("window.scrollBy(0, 800)")
                        page.wait_for_timeout(400)
                except Exception:
                    pass
            print(f"  Subcategorie-navigatie klaar, {len(captured_responses)} responses")

            # Network interception: parseer PromotionOfferList
            for resp in captured_responses:
                try:
                    body = resp.text()
                    if "PromotionOfferList" not in body:
                        continue
                    _parse_plus_network(json.loads(body), results, seen)
                except Exception as e:
                    print(f"  parse fout: {e}")
            print(f"  Network resultaat: {len(results)} producten")

            # DOM scraping: nieuwe kaarten toevoegen + was-prijzen aanvullen
            if not results:
                print("  DOM fallback...")
            dom_results, was_map = _parse_plus_dom(page)

            # Vul ontbrekende was-prijzen aan in network-results via DOM was_map
            for d in results:
                if d.get("was") is None:
                    key = d["naam"].lower()
                    if key in was_map:
                        d["was"] = was_map[key]

            for d in dom_results:
                key = d["naam"].lower()
                if key not in seen:
                    seen.add(key)
                    results.append(d)

            browser.close()

    except Exception as e:
        print(f"  FOUT Plus (Playwright): {e}")

    return results


# ──────────────────────────────────────────────
# Main
# ──────────────────────────────────────────────

SCRAPERS = [
    ("Albert Heijn", scrape_ah),
    ("Jumbo",        scrape_jumbo),
    # ("Lidl",         scrape_lidl),  # tijdelijk uitgeschakeld (Vision API kost geld)
    ("Aldi",         scrape_aldi),
    ("Plus",         scrape_plus),
    ("Dirk",         scrape_dirk),
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
  .super-aldi {{ background: #fff8e1; color: #e65100; }}
  .super-plus {{ background: #e8f5e9; color: #2e7d32; }}
  .super-dirk {{ background: #fce4ec; color: #c62828; }}
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
  const superClass = s => s === 'Albert Heijn' ? 'super-ah' : s === 'Jumbo' ? 'super-jumbo' : s === 'Lidl' ? 'super-lidl' : s === 'Aldi' ? 'super-aldi' : s === 'Plus' ? 'super-plus' : s === 'Dirk' ? 'super-dirk' : '';
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

    cats = sorted({d["desc"] for d in enriched if d.get("desc") and d["desc"] != "Niet interessant"})
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
            if d["desc"] == "Weekaanbieding":
                d["desc"] = classify_by_name(d.get("naam", ""))
            if d["desc"] in HIDDEN_CATEGORIES:
                d["desc"] = "Niet interessant"
        status = "OK" if deals else "⚠ GEEN DEALS"
        print(f"  {len(deals)} deals gevonden  [{status}]")
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

    print(f"\n{'='*40}")
    print(f"SAMENVATTING  {datum}")
    print(f"{'='*40}")
    counts = {}
    for d in all_deals:
        counts[d["supermarkt"]] = counts.get(d["supermarkt"], 0) + 1
    for naam, _ in SCRAPERS:
        n = counts.get(naam, 0)
        flag = "  ⚠ CONTROLEER" if n == 0 else ""
        print(f"  {naam:<20} {n:>4} deals{flag}")
    print(f"  {'TOTAAL':<20} {len(all_deals):>4} deals")
    print(f"{'='*40}")


if __name__ == "__main__":
    main()
