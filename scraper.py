#!/usr/bin/env python3
"""
Latvia Fuel Price Scraper
Scrapes current fuel prices from major Latvian gas station networks
using Playwright (headless Chromium). Saves results to data/prices.json.

Usage:
    python scraper.py            # real scrape
    python scraper.py --demo     # generate sample data without scraping
    python scraper.py --debug    # real scrape + save screenshots to data/
"""

import asyncio
import io
import json
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

# Force UTF-8 stdout so emoji in print() work on Windows
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

from playwright.async_api import (
    async_playwright,
    Page,
    Response,
    TimeoutError as PlaywrightTimeout,
)


# ---------------------------------------------------------------------------
# Error types
# ---------------------------------------------------------------------------

class ScraperError(Exception):
    """Structured scraping failure with context."""

    def __init__(
        self,
        station: str,
        reason: str,
        *,
        recoverable: bool = True,
        http_status: int | None = None,
    ) -> None:
        self.station    = station
        self.reason     = reason
        self.recoverable = recoverable
        self.http_status = http_status
        super().__init__(f"[{station}] {reason}")

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

DATA_DIR = Path("data")
DATA_DIR.mkdir(exist_ok=True)

SOURCES: dict[str, dict] = {
    "circle_k": {
        "name":     "Circle K",
        "url":      "https://www.circlek.lv/degvielas-cenas",
        "logo":     "images/Circle_K.png",
        "discounts": {"EXTRA": 0.07, "Mans Rimi": 0.04},
        "amenities": ["coffee", "food", "car_wash", "tire"],
        "address":  "Vairāk nekā 60 AZS Latvijā",
        # CSS selectors tried in order (most specific → most generic).
        # circlek.lv runs Drupal 9/10 — prices live inside a body-field table.
        "selectors": [
            ".field--name-body table tr",           # Drupal 9/10 body field
            ".field-name-body table tr",            # Drupal 7 body field
            "article.node--type-page table tr",     # Drupal node template
            ".view-content table tr",               # Views module output
            ".region-content table tr",             # Generic Drupal region
            ".layout-container table tr",           # Drupal Layout Builder
            "main table tr",
            "table tr",                             # last-resort generic
        ],
    },
    "neste": {
        "name":     "Neste",
        "url":      "https://www.neste.lv/lv/content/degvielas-cenas",
        "logo":     "images/Neste.png",
        "discounts": {"Neste Card": 0.06},
        "amenities": ["coffee", "food"],
        "address":  "30+ AZS Latvijā",
        # neste.lv also uses Drupal; prices as a simple table in the body field.
        "selectors": [
            ".field--type-text-with-summary table tr",
            ".field--name-body table tr",
            ".field-name-body table tr",
            "article.node table tr",
            ".content-area table tr",
            "main table tr",
            "table tr",
        ],
    },
    "viada": {
        "name":     "Viada",
        "url":      "https://www.viada.lv/ru/fuel-prices/",
        "logo":     "images/Viada.png",
        # Viada plus — tiered fixed discount (viada.lv, verified Apr 2026):
        #   ≤ 70 l/month → −0.025 €/l
        #   > 70 l/month → −0.035 €/l
        "discounts": {
            "Viada plus (līdz 70 l)": 0.025,
            "Viada plus (virs 70 l)": 0.035,
        },
        "amenities": ["coffee", "tire", "car_wash"],   # free wash every 5th
        "address":  "15+ AZS Latvijā",
        # viada.lv/ru — custom PHP or WP, Russian-language fuel-prices page.
        "selectors": [
            "#fuel-prices table tr",
            ".fuel-prices table tr",
            ".fuel-prices-table tr",
            ".price-table tr",
            "[class*='fuel'] tr",
            "[class*='price'] tr",
            ".entry-content table tr",              # WP page content
            "article table tr",
            "table tr",
        ],
    },
    "virsi": {
        "name":     "Virši",
        "url":      "https://www.virsi.lv/lv/private/fuel/cenas",
        "logo":     "images/Virsi.png",
        "discounts": {"Virši+": 0.05},
        "amenities": ["coffee", "food", "car_wash"],
        "address":  "40+ AZS Latvijā",
        # virsi.lv uses a React SPA — class names may be hashed, but semantic
        # patterns are still predictable.
        "selectors": [
            "[class*='PriceTable'] tr",             # React component pattern
            "[class*='FuelPrice'] tr",
            "[class*='price-table'] tr",
            "[class*='priceTable'] tr",
            ".prices-section table tr",
            "[data-testid*='fuel'] tr",
            "[data-testid*='price'] tr",
            "table tr",
        ],
    },
}

# Maps internal keys to display names and page aliases (lowercase)
FUEL_MAP: dict[str, list[str]] = {
    "95":     ["95", "e95", "super 95", "benzīns 95", "бензин 95", "unleaded 95",
               "euro 95", "ai-95", "95 benzin"],
    "98":     ["98", "e98", "super 98", "premium", "бензин 98", "ai-98", "98 benzin"],
    "D":      ["diesel", "dīzeļdegviela", "дизель", "dieselis", " d ", "hvo",
               "euro diesel", "b7", "b10"],
    "LPG":    ["lpg", "gāze", "autogas", "газ", "сжиженный"],
    "AdBlue": ["adblue", "ad blue", "adblue®"],
}

PRICE_RE = re.compile(r"\b([01]\.\d{3}|[12]\.\d{2,3})\b")

# Keywords that signal a promotional offer on the page
PROMO_KEYWORDS: list[str] = [
    # Latvian
    "akcija", "atlaide", "atlaides", "piedāvājums", "īpašais piedāvājums",
    "nedēļas", "bezmaksas",
    # Russian (Viada page)
    "акция", "скидка", "специальное", "предложение",
    # Lithuanian (kurohudas cross-reference)
    "nuolaida", "akcija",
    # English / generic
    "special", "promo", "offer", "deal", "discount",
]

# "-7 c", "- 7c", "−10 c/l", "-7 centi" → captures the integer cent value
DISCOUNT_CENT_RE = re.compile(
    r"[-−]\s*(\d{1,2})\s*c(?:ent(?:i|u)?)?(?:\s*/\s*l)?",
    re.IGNORECASE,
)
# "-0.07 €", "−0.10€/l" → captures the decimal part
DISCOUNT_EUR_RE = re.compile(
    r"[-−]\s*0[.,](\d{2,3})\s*(?:€|eur)?(?:\s*/\s*l)?",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def parse_price(text: str) -> Optional[float]:
    """Extract the most plausible fuel price from a text fragment."""
    for match in PRICE_RE.finditer(text.replace(",", ".")):
        value = float(match.group(1))
        if 0.40 <= value <= 3.50:          # sanity range (EUR/litre)
            return round(value, 3)
    return None


def match_fuel(text: str) -> Optional[str]:
    """Identify which fuel type the text refers to."""
    lower = text.lower()
    for fuel_key, aliases in FUEL_MAP.items():
        for alias in aliases:
            if alias in lower:
                return fuel_key
    return None


async def with_retry(coro_fn, *, attempts: int = 2, delay: float = 4.0):
    """
    Run an async callable; retry up to `attempts` times on PlaywrightTimeout.
    Any non-timeout exception propagates immediately (no retry).
    """
    last_exc: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            return await coro_fn()
        except PlaywrightTimeout as exc:
            last_exc = exc
            if attempt < attempts:
                print(f"      ↩  timeout on attempt {attempt}, retrying in {delay}s…")
                await asyncio.sleep(delay)
    raise last_exc  # type: ignore[misc]


async def check_page_response(page: Page, name: str) -> None:
    """
    Raise ScraperError if the last navigation returned a non-2xx/3xx status.
    Must be called right after page.goto().
    """
    try:
        resp: Response | None = await page.evaluate(
            "() => ({ status: window.__lastStatus ?? 0 })"
        )
    except Exception:
        return  # can't read — skip check silently

    if resp and isinstance(resp, dict):
        status = resp.get("status", 0)
        if status and status >= 400:
            raise ScraperError(
                name,
                f"HTTP {status} — page blocked or not found",
                recoverable=False,
                http_status=status,
            )


def calculate_trends(current: dict, previous: dict) -> dict:
    """Compare prices with previous run and return trend per fuel."""
    trends: dict[str, str] = {}
    for fuel, price in current.items():
        prev = previous.get(fuel)
        if prev is None:
            trends[fuel] = "stable"
        elif price < prev:
            trends[fuel] = "down"
        elif price > prev:
            trends[fuel] = "up"
        else:
            trends[fuel] = "stable"
    return trends


# ---------------------------------------------------------------------------
# Promo / Flash-deal detection
# ---------------------------------------------------------------------------

def _parse_discount(text: str) -> float:
    """
    Extract a promotional discount in EUR from a raw text fragment.
    Priority: cent notation ("-7 c" → 0.07) > EUR notation ("-0.07€" → 0.07).
    Returns 0.0 when no discount can be determined.
    """
    m = DISCOUNT_CENT_RE.search(text)
    if m:
        return round(int(m.group(1)) / 100, 3)

    m = DISCOUNT_EUR_RE.search(text)
    if m:
        digits = m.group(1)
        return round(float(f"0.{digits}"), 3)

    return 0.0


def _build_promo(raw_text: str, prices: dict[str, float]) -> Optional[dict]:
    """
    Parse a single promo text fragment into a structured promo object.

    Returns a dict with:
      promo_text   — normalised human-readable description (≤ 300 chars)
      discount_eur — discount amount in EUR (e.g. 0.07)
      fuel         — fuel key the promo applies to, or None (= all fuels)
      final_prices — {fuel_key: base_price - discount_eur} for affected fuels
    Returns None when no meaningful discount is found.
    """
    text = " ".join(raw_text.split())          # collapse whitespace
    discount = _parse_discount(text)
    if discount <= 0:
        return None

    fuel = match_fuel(text)

    # Build final_prices: apply discount to matched fuel, or to all non-AdBlue fuels
    target_fuels = [fuel] if (fuel and fuel in prices) else [
        f for f in prices if f != "AdBlue"
    ]
    final_prices = {f: round(prices[f] - discount, 3) for f in target_fuels}

    return {
        "promo_text":   text[:300],
        "discount_eur": discount,
        "fuel":         fuel,
        "final_prices": final_prices,
    }


async def find_promos(page: Page, prices: dict[str, float]) -> list[dict]:
    """
    Scan the *already-loaded* page for promotional offers.

    Two-pass strategy:
      Pass 1 — look inside dedicated promo/banner containers (CSS selectors).
      Pass 2 — scan every line of body text for keyword + discount pattern.

    Returns a deduplicated list of promo objects (may be empty).
    """
    promos: list[dict] = []
    seen: set[str] = set()          # deduplicate by first 80 chars of text

    def _add(raw: str) -> None:
        key = raw.strip().lower()[:80]
        if key in seen:
            return
        seen.add(key)
        promo = _build_promo(raw, prices)
        if promo:
            promos.append(promo)

    # Pass 1 — dedicated promo containers
    promo_selectors = [
        "[class*='promo']", "[class*='akcija']", "[class*='atlaide']",
        "[class*='offer']", "[class*='deal']", "[class*='special']",
        "[class*='campaign']", "[class*='banner']",
        ".notification", ".alert", ".info-box", ".callout",
        "aside", "[role='complementary']",
    ]
    for selector in promo_selectors:
        try:
            for el in await page.query_selector_all(selector):
                text = await el.inner_text()
                lower = text.lower()
                if any(kw in lower for kw in PROMO_KEYWORDS):
                    _add(text)
        except Exception:
            continue

    # Pass 2 — full body text, line by line
    try:
        body = await page.inner_text("body")
        for line in body.splitlines():
            line = line.strip()
            if len(line) < 8:
                continue
            lower = line.lower()
            has_kw       = any(kw in lower for kw in PROMO_KEYWORDS)
            has_discount = bool(
                DISCOUNT_CENT_RE.search(line) or DISCOUNT_EUR_RE.search(line)
            )
            # Only collect lines that have BOTH a keyword and a discount amount
            if has_kw and has_discount:
                _add(line)
    except Exception:
        pass

    if promos:
        print(f"      🎁  {len(promos)} promo(s) found")

    return promos


# ---------------------------------------------------------------------------
# Generic scraper
# ---------------------------------------------------------------------------

async def _navigate(page: Page, url: str, name: str) -> None:
    """Navigate with retry on timeout; raise ScraperError on persistent failure."""
    async def _go():
        resp = await page.goto(url, wait_until="networkidle", timeout=35_000)
        await page.wait_for_timeout(2_000)   # let JS finish rendering
        # Block on obvious HTTP errors returned directly
        if resp and resp.status >= 400:
            raise ScraperError(
                name,
                f"HTTP {resp.status}",
                recoverable=(resp.status not in (403, 404)),
                http_status=resp.status,
            )

    try:
        await with_retry(_go, attempts=2, delay=4.0)
    except ScraperError:
        raise
    except PlaywrightTimeout:
        raise ScraperError(name, "page load timeout after 2 attempts", recoverable=True)
    except Exception as exc:
        raise ScraperError(name, f"navigation error: {exc}", recoverable=True)


async def scrape_page(
    page: Page,
    url: str,
    name: str,
    site_selectors: list[str] | None = None,
) -> dict[str, float]:
    """
    Multi-strategy scraper.

    Strategy 0  Site-specific CSS selectors (passed via `site_selectors`).
    Strategy 1  Block / list / dl elements with class fragments.
    Strategy 2  Raw body text, line by line.

    Returns {fuel_key: price_float}.
    Raises ScraperError on navigation failure.
    """
    prices: dict[str, float] = {}

    await _navigate(page, url, name)

    # ── Strategy 0: site-specific selectors (ordered, most specific first) ──
    if site_selectors:
        for selector in site_selectors:
            try:
                elements = await page.query_selector_all(selector)
                for el in elements:
                    text = await el.inner_text()
                    fuel  = match_fuel(text)
                    price = parse_price(text)
                    if fuel and price and fuel not in prices:
                        prices[fuel] = price
            except Exception:
                continue
            if prices:
                print(f"      selector hit: {selector!r}")
                return prices

    # ── Strategy 1: generic block / list / dl elements ───────────────────────
    block_selectors = [
        ".fuel-price", ".price", "[class*='price']",
        "[class*='fuel']", "[class*='degviela']", "[class*='cena']",
        "[class*='nafta']", "[class*='kuro']",
        "li", "dl dt", "dl dd", ".item",
    ]
    for selector in block_selectors:
        try:
            elements = await page.query_selector_all(selector)
            for el in elements:
                text  = await el.inner_text()
                fuel  = match_fuel(text)
                price = parse_price(text)
                if fuel and price and fuel not in prices:
                    prices[fuel] = price
        except Exception:
            continue
        if prices:
            print(f"      generic selector hit: {selector!r}")
            return prices

    # ── Strategy 2: raw body text ────────────────────────────────────────────
    try:
        body = await page.inner_text("body")
        for line in body.splitlines():
            fuel  = match_fuel(line)
            price = parse_price(line)
            if fuel and price and fuel not in prices:
                prices[fuel] = price
        if prices:
            print("      text-fallback hit")
    except Exception as exc:
        print(f"      body text error: {exc}")

    if not prices:
        raise ScraperError(name, "no prices found by any strategy", recoverable=True)

    return prices


# ---------------------------------------------------------------------------
# Per-site scrapers (use their own selectors, add site-specific fallbacks)
# ---------------------------------------------------------------------------

async def scrape_circle_k(page: Page) -> dict[str, float]:
    src = SOURCES["circle_k"]
    try:
        return await scrape_page(page, src["url"], src["name"], src["selectors"])
    except ScraperError:
        # Extra fallback: Circle K may render via React with data-* attributes
        try:
            prices: dict[str, float] = {}
            els = await page.query_selector_all(
                "[data-fuel-type], [data-product-name], [aria-label*='cena']"
            )
            for el in els:
                text  = await el.inner_text()
                fuel  = match_fuel(text)
                price = parse_price(text)
                if fuel and price:
                    prices[fuel] = price
            if prices:
                return prices
        except Exception:
            pass
        raise


async def scrape_neste(page: Page) -> dict[str, float]:
    src = SOURCES["neste"]
    return await scrape_page(page, src["url"], src["name"], src["selectors"])


async def scrape_viada(page: Page) -> dict[str, float]:
    src = SOURCES["viada"]
    return await scrape_page(page, src["url"], src["name"], src["selectors"])


async def scrape_virsi(page: Page) -> dict[str, float]:
    src = SOURCES["virsi"]
    try:
        return await scrape_page(page, src["url"], src["name"], src["selectors"])
    except ScraperError:
        # Virši SPA fallback: try internal JSON API endpoint
        try:
            prices: dict[str, float] = {}
            resp = await page.evaluate("""async () => {
                const endpoints = ['/api/fuel-prices', '/api/v1/prices', '/lv/api/cenas'];
                for (const ep of endpoints) {
                    try {
                        const r = await fetch(ep);
                        if (r.ok) return r.json();
                    } catch (_) {}
                }
                return null;
            }""")
            if resp and isinstance(resp, list):
                for item in resp:
                    fuel  = match_fuel(str(item.get("type", "") or item.get("name", "")))
                    price = parse_price(str(item.get("price", "") or item.get("value", "")))
                    if fuel and price:
                        prices[fuel] = price
            if prices:
                return prices
        except Exception:
            pass
        raise


SCRAPERS = {
    "circle_k": scrape_circle_k,
    "neste":    scrape_neste,
    "viada":    scrape_viada,
    "virsi":    scrape_virsi,
}


# ---------------------------------------------------------------------------
# Main scraping orchestrator
# ---------------------------------------------------------------------------

async def scrape_all(debug: bool = False) -> dict:
    """Launch Playwright, scrape all stations, save JSON."""
    print(f"\n🔍  Starting scrape — {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")

    # Load previous data for trend comparison
    prev_file = DATA_DIR / "prices.json"
    prev_stations: dict = {}
    if prev_file.exists():
        try:
            prev_stations = json.loads(prev_file.read_text("utf-8")).get("stations", {})
        except Exception:
            pass

    results: dict = {}

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        context = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            locale="lv-LV",
            viewport={"width": 1280, "height": 800},
        )
        page = await context.new_page()

        errors: list[str] = []

        for station_id, source in SOURCES.items():
            print(f"  ⛽  {source['name']} …")
            scraper_fn = SCRAPERS[station_id]

            prices: dict[str, float] = {}
            promos: list[dict] = []
            error_msg: str | None = None

            try:
                prices = await scraper_fn(page)
                promos = await find_promos(page, prices)
                print(f"      ✓  {len(prices)} fuel type(s): {prices}")

            except ScraperError as exc:
                error_msg = exc.reason
                level = "⚠ " if exc.recoverable else "✗ "
                print(f"      {level} {exc}")
                errors.append(f"{source['name']}: {exc.reason}")

                # Try to salvage any prices already on the page via body text
                if exc.recoverable:
                    try:
                        body = await page.inner_text("body")
                        for line in body.splitlines():
                            fuel  = match_fuel(line)
                            price = parse_price(line)
                            if fuel and price and fuel not in prices:
                                prices[fuel] = price
                        if prices:
                            print(f"      ↪  salvaged {len(prices)} price(s) from body text")
                            error_msg = None   # recoverable — clear the error
                    except Exception:
                        pass

            except Exception as exc:
                error_msg = str(exc)
                print(f"      ✗  unexpected error: {exc}")
                errors.append(f"{source['name']}: {exc}")

            if debug:
                try:
                    shot_path = DATA_DIR / f"debug_{station_id}.png"
                    await page.screenshot(path=str(shot_path), full_page=True)
                    print(f"      📸  screenshot → {shot_path}")
                except Exception:
                    pass

            prev_prices = prev_stations.get(station_id, {}).get("prices", {})
            trends = calculate_trends(prices, prev_prices)

            station_record: dict = {
                "name":       source["name"],
                "logo":       source["logo"],
                "url":        source["url"],
                "address":    source["address"],
                "prices":     prices,
                "discounts":  source["discounts"],
                "amenities":  source["amenities"],
                "trends":     trends,
                "promos":     promos,
                "scraped_at": datetime.now().isoformat(),
            }
            if error_msg:
                station_record["error"] = error_msg

            results[station_id] = station_record

        await browser.close()

    # ── Summary ──────────────────────────────────────────────────────────────
    total    = len(SOURCES)
    ok_count = sum(1 for s in results.values() if "error" not in s)
    print(f"\n{'✅' if not errors else '⚠ '}  {ok_count}/{total} stations scraped successfully.")
    if errors:
        print("  Errors:")
        for e in errors:
            print(f"    • {e}")

    output = {
        "scraped_at": datetime.now().isoformat(),
        "stations":   results,
    }
    prev_file.write_text(json.dumps(output, ensure_ascii=False, indent=2), "utf-8")
    print(f"  Saved → {prev_file}\n")
    return output


# ---------------------------------------------------------------------------
# Demo data generator
# ---------------------------------------------------------------------------

def generate_demo() -> dict:
    """Write realistic sample data so the Streamlit UI can be tested offline."""
    now = datetime.now().isoformat()

    demo: dict[str, dict] = {
        "circle_k": {
            "prices":  {"95": 1.714, "98": 1.829, "D": 1.598, "LPG": 0.689, "AdBlue": 0.249},
            "trends":  {"95": "stable", "98": "down", "D": "up", "LPG": "stable"},
            "promos": [
                {
                    "promo_text":   "Akcija! Ar Circle K EXTRA karti -7 c/l degvielai AI-95 un dīzeļdegvielai",
                    "discount_eur": 0.07,
                    "fuel":         "95",
                    "final_prices": {"95": round(1.714 - 0.07, 3), "D": round(1.598 - 0.07, 3)},
                },
                {
                    "promo_text":   "Mans Rimi karte: atlaide -4 c/l pie uzpildes no 30L",
                    "discount_eur": 0.04,
                    "fuel":         None,
                    "final_prices": {"95": round(1.714 - 0.04, 3), "D": round(1.598 - 0.04, 3)},
                },
            ],
        },
        "neste": {
            "prices":  {"95": 1.699, "98": 1.809, "D": 1.579},
            "trends":  {"95": "down", "98": "down", "D": "stable"},
            "promos": [
                {
                    "promo_text":   "Neste Card īpašais piedāvājums: -6 c/l visām degvielas veidiem",
                    "discount_eur": 0.06,
                    "fuel":         None,
                    "final_prices": {
                        "95": round(1.699 - 0.06, 3),
                        "98": round(1.809 - 0.06, 3),
                        "D":  round(1.579 - 0.06, 3),
                    },
                },
            ],
        },
        "viada": {
            "prices":  {"95": 1.709, "D": 1.569},
            "trends":  {"95": "stable", "D": "down"},
            "promos": [
                {
                    "promo_text":   "Viada plus karte: atlaide -2.5 c/l (līdz 70 l mēnesī)",
                    "discount_eur": 0.025,
                    "fuel":         None,
                    "final_prices": {
                        "95": round(1.709 - 0.025, 3),
                        "D":  round(1.569 - 0.025, 3),
                    },
                },
                {
                    "promo_text":   "Viada plus karte: atlaide -3.5 c/l (virs 70 l mēnesī)",
                    "discount_eur": 0.035,
                    "fuel":         None,
                    "final_prices": {
                        "95": round(1.709 - 0.035, 3),
                        "D":  round(1.569 - 0.035, 3),
                    },
                },
            ],
        },
        "virsi": {
            "prices":  {"95": 1.719, "98": 1.839, "D": 1.609, "LPG": 0.679},
            "trends":  {"95": "up", "98": "stable", "D": "stable", "LPG": "down"},
            "promos": [
                {
                    "promo_text":   "Nedēļas nogales akcija: atlaide -10 c/l benzīnam AI-95 sestdienā un svētdienā",
                    "discount_eur": 0.10,
                    "fuel":         "95",
                    "final_prices": {"95": round(1.719 - 0.10, 3)},
                },
                {
                    "promo_text":   "Virši+ karte: -5 c/l visām degvielas veidiem",
                    "discount_eur": 0.05,
                    "fuel":         None,
                    "final_prices": {
                        "95":  round(1.719 - 0.05, 3),
                        "98":  round(1.839 - 0.05, 3),
                        "D":   round(1.609 - 0.05, 3),
                        "LPG": round(0.679 - 0.05, 3),
                    },
                },
            ],
        },
    }

    stations: dict = {}
    for sid, src in SOURCES.items():
        d = demo[sid]
        stations[sid] = {
            "name":       src["name"],
            "logo":       src["logo"],
            "url":        src["url"],
            "address":    src["address"],
            "prices":     d["prices"],
            "discounts":  src["discounts"],
            "amenities":  src["amenities"],
            "trends":     d["trends"],
            "promos":     d["promos"],
            "scraped_at": now,
        }

    output = {"scraped_at": now, "stations": stations}
    out_file = DATA_DIR / "prices.json"
    out_file.write_text(json.dumps(output, ensure_ascii=False, indent=2), "utf-8")
    print(f"✅  Demo data → {out_file}")
    return output


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    if "--demo" in sys.argv:
        generate_demo()
    else:
        debug = "--debug" in sys.argv
        asyncio.run(scrape_all(debug=debug))
