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
import html as _html_mod
import io
import json
import re
import sys
import urllib.error
import urllib.request
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
        "url":      "https://www.viada.lv/zemakas-degvielas-cenas/",
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
        "url":      "https://www.virsi.lv/lv/privatpersonam/elektriba/degvielas-cena",
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
    # IMPORTANT: order matters — more specific aliases must appear BEFORE short
    # single-digit ones ("95", "98") to avoid false matches inside price strings.
    "LPG":    ["autogāze", "lpg", "autogas", "gāze", "газ", "сжиженный"],
    "AdBlue": ["adblue", "ad blue", "adblue®"],
    "D":      ["dīzeļdegviela", "futura d", "diesel", "дизель", "dieselis",
               "dmiles", "euro diesel", "b7", "b10"],
    "95":     ["95miles", "futura 95", "benzīns 95", "бензин 95", "unleaded 95",
               "euro 95", "ai-95", "e95", "super 95", "95e"],
    "98":     ["98miles", "futura 98", "super 98", "бензин 98", "ai-98", "e98",
               "premium", "98e"],
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


_VIRSI_LABEL_MAP: dict[str, str] = {
    "dd":  "D",    # Virši diesel brand
    "95e": "95",   # Virši 95E brand
    "98e": "98",   # Virši 98E brand
}


async def scrape_virsi(page: Page) -> dict[str, float]:
    """
    Virši SPA scraper.

    The React page renders each fuel card with the fuel label and price on
    SEPARATE lines (e.g. "DD\\n2.147\\n…"), so a single-line scan misses
    diesel.  We use two passes and merge results:

    Pass 1 — generic scrape_page (catches 95E, 98E, LPG, AdBlue via .price).
    Pass 2 — sliding-window over body text lines; also handles Virši-specific
             short labels ("DD", "95E", "98E") that aren't in the global FUEL_MAP.
    """
    src    = SOURCES["virsi"]
    prices: dict[str, float] = {}

    # Pass 1: generic scraper (fast, catches most fuels)
    try:
        prices = await scrape_page(page, src["url"], src["name"], src["selectors"])
    except ScraperError:
        pass

    # Pass 2: sliding window — fills gaps (especially "DD" = diesel)
    try:
        body  = await page.inner_text("body")
        lines = [ln.strip() for ln in body.splitlines() if ln.strip()]
        for i, line in enumerate(lines):
            # Check Virši-specific short labels first
            virsi_fuel = _VIRSI_LABEL_MAP.get(line.lower())
            # Fall back to global alias match (wrapping in spaces catches " dd ")
            fuel = virsi_fuel or match_fuel(line)
            if not fuel:
                continue
            for j in range(i + 1, min(i + 5, len(lines))):
                price = parse_price(lines[j])
                if price:
                    if fuel not in prices:   # don't overwrite Pass 1 result
                        prices[fuel] = price
                    break
    except Exception:
        pass

    if prices:
        return prices

    raise ScraperError(src["name"], "no prices found on Virši SPA", recoverable=True)


SCRAPERS = {
    "circle_k": scrape_circle_k,
    "neste":    scrape_neste,
    "viada":    scrape_viada,
    "virsi":    scrape_virsi,
}

# Static fallback prices used when both HTTP and Playwright fail
# (e.g. Virši on Streamlit Cloud where Chromium is unavailable).
# Updated to reflect April 2026 actual scraped values.
_STATIC_PRICES: dict[str, dict[str, float]] = {
    "virsi": {"95": 1.854, "98": 1.907, "D": 2.147, "LPG": 1.085, "AdBlue": 0.845},
}

# Side-effect cache: filled by _scrape_viada_http, consumed by scrape_all()
_viada_adus_prices: dict[str, float] = {}


# ---------------------------------------------------------------------------
# Lightweight HTTP scraper (no browser required)
# ---------------------------------------------------------------------------

_HTTP_HEADERS: dict[str, str] = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept":          "text/html,application/xhtml+xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "lv-LV,lv;q=0.9,ru;q=0.8,en;q=0.7",
    "Accept-Encoding": "identity",
    "Connection":      "keep-alive",
}

_TAG_RE  = re.compile(r"<[^>]+>")
_TR_RE   = re.compile(r"<tr\b[^>]*>(.*?)</tr>",       re.DOTALL | re.IGNORECASE)
_CELL_RE = re.compile(r"<t[dh]\b[^>]*>(.*?)</t[dh]>", re.DOTALL | re.IGNORECASE)


def _http_get(url: str, timeout: int = 25) -> str:
    """Fetch URL with browser-like headers. Returns decoded HTML string."""
    req = urllib.request.Request(url, headers=_HTTP_HEADERS)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        charset = resp.headers.get_content_charset("utf-8")
        return resp.read().decode(charset, errors="replace")


def _prices_from_html(raw_html: str) -> dict[str, float]:
    """
    Two-pass HTML → prices extractor (stdlib only, no BeautifulSoup).

    Pass 1 — find <tr> rows; match_fuel on the FIRST cell only (avoids matching
              "95" inside a price like "1.095"), parse_price from other cells.
    Pass 2 — strip all tags, scan plain-text lines split at | : – delimiters.
    """
    prices: dict[str, float] = {}

    # Pass 1: table rows — fuel from first cell, price from subsequent cells
    for tr_m in _TR_RE.finditer(raw_html):
        raw_cells = _CELL_RE.findall(tr_m.group(1))
        cells = [
            _html_mod.unescape(_TAG_RE.sub(" ", c))
            .replace("\xa0", " ").replace("\u2009", " ")  # non-breaking / thin space → space
            .strip()
            for c in raw_cells
        ]
        cells = [c for c in cells if c]

        if len(cells) < 2:
            continue

        fuel = match_fuel(cells[0])
        if not fuel:
            continue

        for cell in cells[1:]:
            price = parse_price(cell)
            if price and fuel not in prices:
                prices[fuel] = price
                break

    if prices:
        return prices

    # Pass 2: plain-text fallback
    plain = _html_mod.unescape(_TAG_RE.sub(" ", raw_html))
    for line in plain.splitlines():
        line = line.strip()
        if len(line) < 4:
            continue
        parts = re.split(r"[|:–—]", line, maxsplit=1)
        fuel  = match_fuel(parts[0])
        price = parse_price(parts[1] if len(parts) > 1 else line)
        if fuel and price and fuel not in prices:
            prices[fuel] = price

    return prices


_IMG_RE = re.compile(r'<img[^>]+src=["\']([^"\']+)["\']', re.IGNORECASE)


def _viada_fuel_from_img(src_url: str) -> Optional[str]:
    """Map a Viada fuel image filename to an internal fuel key."""
    name = src_url.lower().split("/")[-1]     # e.g. "petrol_95ecto_new.png"
    if "98" in name:
        return "98"
    if "95" in name:
        return "95"
    if "_d_" in name or name.startswith("petrol_d"):
        return "D"
    if "gaze" in name or "lpg" in name or "gas" in name:
        return "LPG"
    return None


def _scrape_viada_http(raw: str) -> dict[str, float]:
    """
    Viada-specific parser: fuel type is encoded in an <img> src in the first
    table cell.

    viada.lv/zemakas-degvielas-cenas/ lists prices for two station types:
      • ADUS — automated stations (no staff, no loyalty cards); cheaper.
      • DUS  — standard full-service stations; higher price, most of the network.

    We prefer DUS prices. ADUS prices are used only as a fallback when no
    DUS price exists for a given fuel type (e.g. LPG, 95, 98).
    """
    dus_prices:  dict[str, float] = {}   # standard DUS stations
    adus_prices: dict[str, float] = {}   # automated ADUS-only stations

    for tr_m in _TR_RE.finditer(raw):
        raw_cells = _CELL_RE.findall(tr_m.group(1))
        if len(raw_cells) < 2:
            continue

        img_m = _IMG_RE.search(raw_cells[0])
        if not img_m:
            continue

        fuel = _viada_fuel_from_img(img_m.group(1))
        if not fuel:
            continue

        price_text = _html_mod.unescape(_TAG_RE.sub(" ", raw_cells[1])).strip()
        price = parse_price(price_text)
        if not price:
            continue

        # Determine station type from the 3rd column (station list).
        # A row is ADUS-only when every station name starts with "ADUS".
        # A row has standard DUS when at least one name starts with "DUS "
        # (not preceded by "A", e.g. "DUS Dārzciema").
        is_adus_only = True
        if len(raw_cells) >= 3:
            stations_upper = _TAG_RE.sub(" ", raw_cells[2]).upper()
            # True DUS station name: "DUS " not preceded by "A"
            has_true_dus = bool(re.search(r"(?<!A)DUS\s", stations_upper))
            if has_true_dus:
                is_adus_only = False

        if is_adus_only:
            if fuel not in adus_prices or price < adus_prices[fuel]:
                adus_prices[fuel] = price
        else:
            if fuel not in dus_prices or price < dus_prices[fuel]:
                dus_prices[fuel] = price

    # Persist ADUS prices for scrape_all() to attach to the station record
    global _viada_adus_prices
    _viada_adus_prices = dict(adus_prices)

    # Merge: DUS prices take priority; ADUS fills gaps
    prices = {**adus_prices}
    prices.update(dus_prices)
    return prices


def scrape_station_http(station_id: str) -> dict[str, float]:
    """
    Fetch and parse a station's price page using plain HTTP GET.
    No browser / JavaScript required — works on Streamlit Cloud.
    Raises ScraperError when the page is inaccessible or prices cannot be found.
    """
    src = SOURCES[station_id]
    try:
        raw = _http_get(src["url"])
    except urllib.error.HTTPError as exc:
        raise ScraperError(station_id, f"HTTP {exc.code}", recoverable=(exc.code < 500))
    except Exception as exc:
        raise ScraperError(station_id, f"HTTP fetch failed: {exc}", recoverable=True)

    # Use a station-specific parser when the generic one won't work
    if station_id == "viada":
        prices = _scrape_viada_http(raw)
    else:
        prices = _prices_from_html(raw)

    if not prices:
        raise ScraperError(
            station_id,
            "HTTP: no prices found (page may require JavaScript)",
            recoverable=True,
        )

    print(f"      ✅  HTTP — {len(prices)} types: {prices}")
    return prices


# ---------------------------------------------------------------------------
# Main scraping orchestrator
# ---------------------------------------------------------------------------

async def scrape_all(debug: bool = False) -> dict:
    """
    Two-phase scraper:
      Phase 1 — lightweight HTTP GET for every station (no browser needed).
      Phase 2 — Playwright only for stations where HTTP failed.
    Saves data/prices.json and returns the result dict.
    """
    print(f"\n🔍  Starting scrape — {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")

    # Load previous data for trend comparison
    prev_file = DATA_DIR / "prices.json"
    prev_stations: dict = {}
    if prev_file.exists():
        try:
            prev_stations = json.loads(prev_file.read_text("utf-8")).get("stations", {})
        except Exception:
            pass

    # ── Phase 1: HTTP scraping (no browser) ──────────────────────────────────
    print("  📡  Phase 1 — HTTP scraping …")
    http_prices: dict[str, dict[str, float]] = {}
    http_errors: dict[str, str]              = {}

    for station_id, source in SOURCES.items():
        print(f"  ⛽  {source['name']} (HTTP) …")
        try:
            http_prices[station_id] = scrape_station_http(station_id)
        except ScraperError as exc:
            http_errors[station_id] = exc.reason
            print(f"      ⚠  {exc.reason}")
        except Exception as exc:
            http_errors[station_id] = str(exc)
            print(f"      ✗  {exc}")

    pw_needed = [sid for sid in SOURCES if sid not in http_prices]

    # ── Phase 2: Playwright for stations where HTTP failed ────────────────────
    pw_prices: dict[str, dict[str, float]] = {}
    pw_promos: dict[str, list[dict]]        = {}
    pw_errors: dict[str, str]               = {}

    if pw_needed:
        print(f"\n  🌐  Phase 2 — Playwright for: {', '.join(pw_needed)} …")
        try:
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

                for station_id in pw_needed:
                    source     = SOURCES[station_id]
                    scraper_fn = SCRAPERS[station_id]
                    print(f"  ⛽  {source['name']} (Playwright) …")

                    try:
                        prices = await scraper_fn(page)
                        promos = await find_promos(page, prices)
                        pw_prices[station_id] = prices
                        pw_promos[station_id] = promos
                        print(f"      ✓  {len(prices)} type(s): {prices}")

                    except ScraperError as exc:
                        pw_errors[station_id] = exc.reason
                        print(f"      {'⚠' if exc.recoverable else '✗'}  {exc}")
                        # Try text salvage
                        if exc.recoverable:
                            try:
                                body = await page.inner_text("body")
                                salvaged: dict[str, float] = {}
                                for line in body.splitlines():
                                    fuel  = match_fuel(line)
                                    price = parse_price(line)
                                    if fuel and price and fuel not in salvaged:
                                        salvaged[fuel] = price
                                if salvaged:
                                    pw_prices[station_id] = salvaged
                                    del pw_errors[station_id]
                                    print(f"      ↪  salvaged {len(salvaged)} prices")
                            except Exception:
                                pass

                    except Exception as exc:
                        pw_errors[station_id] = str(exc)
                        print(f"      ✗  {exc}")

                    if debug:
                        try:
                            shot = DATA_DIR / f"debug_{station_id}.png"
                            await page.screenshot(path=str(shot), full_page=True)
                            print(f"      📸  {shot}")
                        except Exception:
                            pass

                await browser.close()

        except Exception as exc:
            print(f"  ✗  Playwright unavailable: {exc}")
            for sid in pw_needed:
                if sid not in pw_prices:
                    pw_errors[sid] = f"Playwright error: {exc}"

    # ── Assemble results ──────────────────────────────────────────────────────
    results: dict = {}
    all_errors = {**http_errors, **pw_errors}

    for station_id, source in SOURCES.items():
        prices    = http_prices.get(station_id) or pw_prices.get(station_id) or {}
        promos    = pw_promos.get(station_id, [])
        error_msg = all_errors.get(station_id) if not prices else None

        prev_prices = prev_stations.get(station_id, {}).get("prices", {})

        # Graceful fallback when live scraping failed (e.g. Playwright unavailable):
        # 1st — use prices from the previous successful run (prev_stations)
        # 2nd — use hardcoded static prices (_STATIC_PRICES)
        # In both cases clear the error so the UI shows prices, not an error banner.
        if not prices:
            cached = prev_prices or _STATIC_PRICES.get(station_id, {})
            if cached:
                prices    = cached
                promos    = prev_stations.get(station_id, {}).get("promos", promos)
                error_msg = None

        trends = calculate_trends(prices, prev_prices)

        record: dict = {
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
            record["error"] = error_msg
        # Attach ADUS prices for Viada so the UI can show both tiers
        if station_id == "viada" and _viada_adus_prices:
            record["adus_prices"] = dict(_viada_adus_prices)

        results[station_id] = record

    # ── Summary ───────────────────────────────────────────────────────────────
    ok_count = sum(1 for s in results.values() if "error" not in s and s.get("prices"))
    total    = len(SOURCES)
    print(f"\n{'✅' if ok_count == total else '⚠ '}  {ok_count}/{total} scraped successfully.")

    output = {"scraped_at": datetime.now().isoformat(), "stations": results}
    prev_file.write_text(json.dumps(output, ensure_ascii=False, indent=2), "utf-8")
    print(f"  Saved → {prev_file}\n")
    return output


# ---------------------------------------------------------------------------
# Demo data generator
# ---------------------------------------------------------------------------

def generate_demo() -> dict:
    """Write realistic sample data so the Streamlit UI can be tested offline."""
    now = datetime.now().isoformat()

    # ── Prices verified / estimated for April 2026 (Latvia) ──────────────────
    # Neste AI-95 = 1.837 confirmed; other prices proportionally estimated.
    demo: dict[str, dict] = {
        # Prices reflect actual scraped values — April 2026, Latvia
        "circle_k": {
            "prices":  {"95": 1.854, "98": 1.924, "D": 2.144, "LPG": 1.095, "AdBlue": 0.249},
            "trends":  {"95": "stable", "98": "stable", "D": "stable", "LPG": "stable"},
            "promos": [
                {
                    "promo_text":   "Akcija! Ar Circle K EXTRA karti -7 c/l degvielai AI-95 un dīzeļdegvielai",
                    "discount_eur": 0.07,
                    "fuel":         "95",
                    "final_prices": {"95": round(1.854 - 0.07, 3), "D": round(2.144 - 0.07, 3)},
                },
                {
                    "promo_text":   "Mans Rimi karte: atlaide -4 c/l pie uzpildes no 30L",
                    "discount_eur": 0.04,
                    "fuel":         None,
                    "final_prices": {"95": round(1.854 - 0.04, 3), "D": round(2.144 - 0.04, 3)},
                },
            ],
        },
        "neste": {
            "prices":  {"95": 1.837, "98": 1.907, "D": 2.147},
            "trends":  {"95": "stable", "98": "stable", "D": "stable"},
            "promos": [
                {
                    "promo_text":   "Neste Card īpašais piedāvājums: -6 c/l visām degvielas veidiem",
                    "discount_eur": 0.06,
                    "fuel":         None,
                    "final_prices": {
                        "95": round(1.837 - 0.06, 3),
                        "98": round(1.907 - 0.06, 3),
                        "D":  round(2.147 - 0.06, 3),
                    },
                },
            ],
        },
        "viada": {
            "prices":      {"95": 1.747, "98": 1.852, "D": 1.947, "LPG": 0.985},
            "adus_prices": {"95": 1.747, "98": 1.852, "D": 1.817, "LPG": 0.985},
            "trends":  {"95": "stable", "98": "stable", "D": "stable", "LPG": "stable"},
            "promos": [
                {
                    "promo_text":   "Viada plus karte: atlaide -2.5 c/l (līdz 70 l mēnesī)",
                    "discount_eur": 0.025,
                    "fuel":         None,
                    "final_prices": {
                        "95": round(1.747 - 0.025, 3),
                        "D":  round(1.947 - 0.025, 3),
                    },
                },
                {
                    "promo_text":   "Viada plus karte: atlaide -3.5 c/l (virs 70 l mēnesī)",
                    "discount_eur": 0.035,
                    "fuel":         None,
                    "final_prices": {
                        "95": round(1.747 - 0.035, 3),
                        "D":  round(1.947 - 0.035, 3),
                    },
                },
            ],
        },
        "virsi": {
            "prices":  {"95": 1.854, "98": 1.907, "D": 2.147, "LPG": 1.085, "AdBlue": 0.845},
            "trends":  {"95": "stable", "98": "stable", "D": "stable", "LPG": "stable"},
            "promos": [
                {
                    "promo_text":   "Nedēļas nogales akcija: atlaide -10 c/l benzīnam AI-95 sestdienā un svētdienā",
                    "discount_eur": 0.10,
                    "fuel":         "95",
                    "final_prices": {"95": round(1.854 - 0.10, 3)},
                },
                {
                    "promo_text":   "Virši+ karte: -5 c/l visām degvielas veidiem",
                    "discount_eur": 0.05,
                    "fuel":         None,
                    "final_prices": {
                        "95":  round(1.854 - 0.05, 3),
                        "98":  round(1.907 - 0.05, 3),
                        "D":   round(2.147 - 0.05, 3),
                        "LPG": round(1.085 - 0.05, 3),
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
        try:
            asyncio.run(scrape_all(debug=debug))
        except Exception as exc:
            # On cloud deployments Playwright / Chromium may be unavailable.
            # Fall back to demo data so the UI always has something to show.
            print(
                f"⚠  Real scrape failed ({exc}). "
                "Falling back to demo data.",
                file=sys.stderr,
            )
            generate_demo()
            sys.exit(0)
