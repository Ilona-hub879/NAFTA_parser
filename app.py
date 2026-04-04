"""
Degvielas Cenas Latvijā — Streamlit dashboard
Visualises fuel prices scraped from Latvian gas station networks.
Design: DESIGN_SYSTEM.md (anthracite / emerald palette, Syne + Inter fonts).
"""

import base64
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import streamlit as st

import os
import subprocess

# Эта функция принудительно устанавливает браузер Playwright на сервере
def install_playwright():
    try:
        # Проверяем, установлен ли уже браузер (чтобы не качать каждый раз)
        subprocess.run(["playwright", "install", "chromium"], check=True)
    except Exception as e:
        print(f"Error installing playwright: {e}")

# Запускаем установку
install_playwright()

# ── must be the very first Streamlit call ─────────────────────────────────
st.set_page_config(
    page_title="Degvielas Cenas | Latvija",
    page_icon="⛽",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DATA_FILE = Path("data/prices.json")

FUEL_LABELS: dict[str, str] = {
    "95":     "AI-95",
    "98":     "AI-98",
    "D":      "Dīzelis",
    "LPG":    "LPG",
    "AdBlue": "AdBlue",
}

AMENITY_ICONS: dict[str, str] = {
    "coffee":   "☕",
    "food":     "🍔",
    "tire":     "🔩",   # 🛞 (Unicode 14.0) renders blank on older systems
    "car_wash": "🧼",
}

# Human-readable labels used in sidebar checkboxes (Latvian)
AMENITY_LABELS: dict[str, str] = {
    "coffee":   "☕ Kafija",
    "food":     "🍔 Ēdiens",
    "tire":     "🔩 Riepas",
    "car_wash": "🧼 Mazgātava",
}

TREND_DATA: dict[str, tuple[str, str]] = {
    "up":     ("↑", "#ff4444"),
    "down":   ("↓", "#00ff7f"),
    "stable": ("→", "#9ca3af"),
}

LOYALTY_OPTIONS: dict[str, dict] = {
    "Nav kartes":             {"station": None,       "discount": 0.00},
    "Circle K EXTRA":         {"station": "circle_k", "discount": 0.07},
    "Mans Rimi":              {"station": "circle_k", "discount": 0.04},
    "Neste Card":             {"station": "neste",    "discount": 0.06},
    "Viada plus (līdz 70 l)": {"station": "viada",   "discount": 0.025},
    "Viada plus (virs 70 l)": {"station": "viada",   "discount": 0.035},
    "Virši+":                 {"station": "virsi",    "discount": 0.05},
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _b64(path: str) -> str:
    try:
        data = Path(path).read_bytes()
        ext  = Path(path).suffix.lower().lstrip(".")
        mime = "jpeg" if ext in ("jpg", "jpeg") else ext
        return f"data:image/{mime};base64,{base64.b64encode(data).decode()}"
    except FileNotFoundError:
        return ""


def _freshness(iso: str) -> str:
    try:
        delta = datetime.now() - datetime.fromisoformat(iso)
        mins  = int(delta.total_seconds() / 60)
        if mins < 1:
            return "tikko atjaunots"
        if mins < 60:
            return f"atjaunots {mins} min. atpakaļ"
        hours = mins // 60
        return f"atjaunots {hours} h atpakaļ"
    except Exception:
        return "nezināms laiks"


def _run_scraper(args: list[str]) -> tuple[bool, str]:
    """
    Run scraper.py with given args via the same Python interpreter.
    Returns (success, stderr_excerpt).
    """
    try:
        result = subprocess.run(
            [sys.executable, "scraper.py"] + args,
            capture_output=True,
            text=True,
            timeout=180,
            encoding="utf-8",
            errors="replace",
        )
        return result.returncode == 0, result.stderr[:400]
    except subprocess.TimeoutExpired:
        return False, "Timeout (180 s)"
    except Exception as exc:
        return False, str(exc)


def bootstrap_if_needed() -> bool:
    """
    If prices.json is missing, auto-generate demo data so the UI
    always has something to show on first launch.

    Returns True if bootstrap was performed (caller should show a banner).
    """
    if DATA_FILE.exists():
        return False

    DATA_FILE.parent.mkdir(parents=True, exist_ok=True)

    with st.spinner("Pirmā palaišana — ģenerē demonstrācijas datus…"):
        ok, err = _run_scraper(["--demo"])

    if not ok:
        st.error(
            f"Neizdevās ģenerēt datus: {err}. "
            "Palaid `python scraper.py --demo` manuāli."
        )
        st.stop()

    return True


def load_data() -> tuple[dict, str | None]:
    """
    Load prices.json.
    Returns (data_dict, error_message | None).
    """
    if not DATA_FILE.exists():
        return {}, (
            f"Fails `{DATA_FILE}` nav atrasts. "
            "Palaid `python scraper.py --demo` vai nospied **🔄 Atjaunot cenas**."
        )
    try:
        raw = DATA_FILE.read_text("utf-8")
    except OSError as exc:
        return {}, f"Nevar lasīt failu: {exc}"

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        return {}, (
            f"Datu fails ir bojāts (JSON kļūda: {exc}). "
            "Palaid `python scraper.py --demo`, lai ģenerētu jaunu failu."
        )

    if not isinstance(data, dict) or "stations" not in data:
        return {}, "Nepareizs datu formāts — trūkst `stations` atslēgas."

    return data, None


# ---------------------------------------------------------------------------
# CSS injection
# ---------------------------------------------------------------------------

def inject_css() -> None:
    bg_b64 = _b64("images/backgraund4.jpg")
    bg_layer = (
        f"url('{bg_b64}') center/cover fixed no-repeat"
        if bg_b64 else "#121212"
    )

    st.markdown(f"""
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600&family=Syne:wght@600;700;800&display=swap');

/* ── Base ── */
html, body, [class*="css"] {{
    font-family: 'Inter', sans-serif !important;
    color: #e5e7eb;
}}
.stApp {{
    background:
        radial-gradient(circle at top left,  rgba(15,23,42,0.92), transparent 55%),
        radial-gradient(circle at bottom right, rgba(15,23,42,0.75), transparent 55%),
        {bg_layer};
    background-color: #121212;
}}
.block-container {{
    padding-top: 1.5rem !important;
    padding-bottom: 3rem !important;
    max-width: 1200px;
}}

/* ── Sidebar ── */
[data-testid="stSidebar"] {{
    background: rgba(18,18,18,0.97) !important;
    border-right: 1px solid rgba(255,255,255,0.05) !important;
}}
[data-testid="stSidebar"] label,
[data-testid="stSidebar"] p,
[data-testid="stSidebar"] span {{
    color: #e5e7eb !important;
    font-family: 'Inter', sans-serif !important;
}}

/* ── Headings ── */
h1, h2, h3 {{
    font-family: 'Syne', sans-serif !important;
    color: #ffffff !important;
}}

/* ── Buttons ── */
.stButton > button {{
    background: rgba(0,255,127,0.10) !important;
    border: 1px solid #00ff7f !important;
    color: #ffffff !important;
    font-family: 'Syne', sans-serif !important;
    font-weight: 700 !important;
    border-radius: 999px !important;
    padding: 0.45rem 1.4rem !important;
    transition: all .3s ease !important;
}}
.stButton > button:hover {{
    background: #00ff7f !important;
    color: #121212 !important;
    box-shadow: 0 0 20px rgba(0,255,127,.4) !important;
}}

/* ── Radio ── */
.stRadio > div {{
    gap: 8px;
}}
.stRadio label {{
    background: rgba(255,255,255,0.04);
    border: 1px solid rgba(255,255,255,0.07);
    border-radius: 8px;
    padding: 6px 14px;
    color: #e5e7eb !important;
    font-family: 'Inter', sans-serif !important;
    font-size: .9rem;
    transition: all .2s;
    cursor: pointer;
}}
.stRadio label:has(input:checked) {{
    border-color: #00ff7f;
    background: rgba(0,255,127,0.12);
    color: #00ff7f !important;
}}

/* ── Selectbox / multiselect ── */
[data-baseweb="select"] > div {{
    background: rgba(255,255,255,0.03) !important;
    border: 1px solid rgba(255,255,255,0.07) !important;
    border-radius: 12px !important;
    color: #e5e7eb !important;
}}
[data-baseweb="tag"] {{
    background: rgba(0,255,127,0.15) !important;
    border: 1px solid #00ff7f !important;
    color: #00ff7f !important;
}}

/* ── Metric ── */
[data-testid="metric-container"] {{
    background: rgba(2,6,23,0.85);
    backdrop-filter: blur(14px);
    border: 1px solid rgba(250,204,21,0.35);
    border-radius: 16px;
    padding: 18px 20px;
}}
[data-testid="metric-container"] label {{
    color: #9ca3af !important;
    font-size: .75rem !important;
    text-transform: uppercase;
    letter-spacing: .08em;
}}
[data-testid="stMetricValue"] {{
    color: #ffffff !important;
    font-family: 'Syne', sans-serif !important;
    font-size: 1.8rem !important;
    font-weight: 800 !important;
}}
[data-testid="stMetricDelta"] {{
    font-size: .8rem !important;
}}

/* ── Divider ── */
hr {{
    border-color: rgba(255,255,255,0.05) !important;
}}

/* ── Alert/info ── */
.stAlert {{
    background: rgba(2,6,23,0.85) !important;
    border: 1px solid rgba(250,204,21,0.35) !important;
    color: #e5e7eb !important;
    border-radius: 12px !important;
}}

/* ── Checkbox ── */
.stCheckbox label {{ color: #e5e7eb !important; }}

/* ── Custom cards ── */
.glass-card {{
    background: rgba(2,6,23,0.85);
    backdrop-filter: blur(14px);
    -webkit-backdrop-filter: blur(14px);
    border: 1px solid rgba(250,204,21,0.35);
    border-radius: 18px;
    box-shadow: 0 18px 45px rgba(0,0,0,0.7);
    padding: 22px 24px;
    margin-bottom: 14px;
    transition: transform .3s ease, box-shadow .3s ease;
}}
.glass-card:hover {{
    transform: translateY(-5px);
    box-shadow: 0 24px 55px rgba(0,0,0,0.85);
}}
.station-logo {{
    width: 80px;
    height: 40px;
    object-fit: contain;
    margin-bottom: 8px;
}}
.station-name {{
    font-family: 'Syne', sans-serif;
    font-size: 1.15rem;
    font-weight: 700;
    color: #ffffff;
    margin: 4px 0;
}}
.price-main {{
    font-family: 'Syne', sans-serif;
    font-size: 2.2rem;
    font-weight: 800;
    color: #ffffff;
    line-height: 1;
}}
.price-unit {{
    font-size: .85rem;
    color: #6b7280;
    margin-left: 2px;
}}
.price-discount {{
    font-family: 'Syne', sans-serif;
    font-size: 1.3rem;
    font-weight: 700;
    color: #00ff7f;
}}
.gift-row {{
    font-size: .78rem;
    color: #9ca3af;
    margin-top: 2px;
}}
.gift-badge {{
    display: inline-block;
    background: rgba(0,255,127,0.12);
    border: 1px solid rgba(0,255,127,0.35);
    border-radius: 8px;
    padding: 3px 10px;
    font-size: .75rem;
    color: #00ff7f;
    margin-top: 6px;
}}
.trend-badge {{
    font-size: 1rem;
    font-weight: 700;
    margin-left: 6px;
}}
.freshness {{
    font-size: .68rem;
    color: #4b5563;
    letter-spacing: .04em;
    margin-top: 10px;
}}
.amenities {{
    font-size: .95rem;
    margin-top: 8px;
    letter-spacing: .04em;
}}
.section-header {{
    font-family: 'Syne', sans-serif;
    font-size: 1.4rem;
    font-weight: 700;
    color: #ffffff;
    margin-bottom: 4px;
}}
.section-header .accent {{
    color: #00ff7f;
}}
.analytic-box {{
    background: rgba(2,6,23,0.85);
    backdrop-filter: blur(14px);
    border: 1px solid rgba(250,204,21,0.35);
    border-radius: 16px;
    padding: 20px;
    text-align: center;
}}
.analytic-value {{
    font-family: 'Syne', sans-serif;
    font-size: 1.9rem;
    font-weight: 800;
    color: #00ff7f;
}}
.analytic-label {{
    font-size: .72rem;
    color: #6b7280;
    text-transform: uppercase;
    letter-spacing: .1em;
    margin-top: 4px;
}}
.top-badge {{
    font-size: .7rem;
    text-transform: uppercase;
    letter-spacing: .12em;
    color: #00ff7f;
    background: rgba(0,255,127,0.1);
    border: 1px solid rgba(0,255,127,0.25);
    border-radius: 6px;
    padding: 2px 8px;
    display: inline-block;
    margin-bottom: 6px;
}}
.no-data {{
    color: #4b5563;
    font-size: .85rem;
    font-style: italic;
}}

/* ── Responsive CSS Grid for station cards ── */
.cards-grid {{
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(260px, 1fr));
    gap: 18px;
    margin-bottom: 28px;
}}
/* ≥ 1400 px — 4 cols */
@media (min-width: 1400px) {{
    .cards-grid {{ grid-template-columns: repeat(4, 1fr); }}
}}
/* 900–1399 px — 3 cols */
@media (min-width: 900px) and (max-width: 1399px) {{
    .cards-grid {{ grid-template-columns: repeat(3, 1fr); }}
}}
/* 580–899 px — 2 cols */
@media (min-width: 580px) and (max-width: 899px) {{
    .cards-grid {{ grid-template-columns: repeat(2, 1fr); }}
}}
/* < 580 px — 1 col */
@media (max-width: 579px) {{
    .cards-grid {{ grid-template-columns: 1fr; }}
    .block-container {{ padding-left: 8px !important; padding-right: 8px !important; }}
    h1 {{ font-size: 1.5rem !important; }}
}}

/* ── Error card variant ── */
.glass-card.error {{
    border-color: rgba(255, 68, 68, 0.35);
    opacity: .8;
}}
.error-badge {{
    display: inline-flex;
    align-items: center;
    gap: 5px;
    background: rgba(255,68,68,0.12);
    border: 1px solid rgba(255,68,68,0.35);
    border-radius: 8px;
    padding: 4px 10px;
    font-size: .72rem;
    color: #ff6b6b;
    margin-top: 8px;
    word-break: break-word;
}}

/* ── Promo card strip ── */
.promo-strip {{
    margin-top: 10px;
    border-top: 1px solid rgba(255,255,255,.06);
    padding-top: 8px;
}}
.promo-item {{
    font-size: .72rem;
    color: #9ca3af;
    line-height: 1.45;
    margin-bottom: 5px;
    padding-left: 8px;
    border-left: 2px solid rgba(0,255,127,.4);
}}
.promo-final {{
    font-family: 'Syne', sans-serif;
    font-size: .95rem;
    font-weight: 700;
    color: #00ff7f;
}}

/* ── Scrollbar ── */
::-webkit-scrollbar {{ width: 5px; }}
::-webkit-scrollbar-track {{ background: #0a0a0a; }}
::-webkit-scrollbar-thumb {{ background: rgba(0,255,127,.3); border-radius: 3px; }}

/* ── Hide default chrome ── */
#MainMenu, footer {{ visibility: hidden; }}
</style>
""", unsafe_allow_html=True)


# ---------------------------------------------------------------------------
# Sidebar
# ---------------------------------------------------------------------------

def render_sidebar() -> tuple[str, str, list[str]]:
    """Returns (selected_fuel, loyalty_card, amenity_filter)."""

    # Brand
    fav_b64 = _b64("images/favikon.jpg")
    if fav_b64:
        st.sidebar.markdown(
            f'<img src="{fav_b64}" style="width:48px;height:48px;'
            f'border-radius:12px;margin-bottom:12px;">',
            unsafe_allow_html=True,
        )
    st.sidebar.markdown(
        '<div style="font-family:\'Syne\',sans-serif;font-size:1.3rem;'
        'font-weight:800;color:#00ff7f;letter-spacing:.05em;">Degvielas<span style="color:#fff;">.</span>lv</div>'
        '<div style="font-size:.68rem;color:#4b5563;text-transform:uppercase;'
        'letter-spacing:.12em;margin-bottom:20px;">Cenas reāllaikā</div>',
        unsafe_allow_html=True,
    )

    st.sidebar.markdown("---")

    # Fuel selector
    st.sidebar.markdown(
        '<p style="font-size:.72rem;color:#6b7280;text-transform:uppercase;'
        'letter-spacing:.1em;margin-bottom:4px;">⛽ Degvielas veids</p>',
        unsafe_allow_html=True,
    )
    fuel_keys  = list(FUEL_LABELS.keys())
    fuel_names = list(FUEL_LABELS.values())
    fuel_choice = st.sidebar.radio(
        "fuel_type", fuel_names, index=0, label_visibility="collapsed"
    )
    selected_fuel = fuel_keys[fuel_names.index(fuel_choice)]

    st.sidebar.markdown("---")

    # Loyalty card
    st.sidebar.markdown(
        '<p style="font-size:.72rem;color:#6b7280;text-transform:uppercase;'
        'letter-spacing:.1em;margin-bottom:4px;">🎁 Lojalitātes karte</p>',
        unsafe_allow_html=True,
    )
    loyalty_choice = st.sidebar.selectbox(
        "loyalty", list(LOYALTY_OPTIONS.keys()), label_visibility="collapsed"
    )

    st.sidebar.markdown("---")

    # Amenities filter
    st.sidebar.markdown(
        '<p style="font-size:.72rem;color:#6b7280;text-transform:uppercase;'
        'letter-spacing:.1em;margin-bottom:4px;">🔧 Pakalpojumi</p>',
        unsafe_allow_html=True,
    )
    amenity_filter: list[str] = []
    cols = st.sidebar.columns(2)
    for i, (key, label) in enumerate(AMENITY_LABELS.items()):
        if cols[i % 2].checkbox(label, key=f"am_{key}"):
            amenity_filter.append(key)

    st.sidebar.markdown("---")

    # Scrape button
    if st.sidebar.button("🔄  Atjaunot cenas", use_container_width=True):
        with st.spinner("Scraping…"):
            try:
                result = subprocess.run(
                    [sys.executable, "scraper.py"],
                    capture_output=True, text=True, timeout=120,
                )
                if result.returncode == 0:
                    st.sidebar.success("Dati atjaunoti!")
                    st.rerun()
                else:
                    st.sidebar.error(f"Kļūda: {result.stderr[:300]}")
            except subprocess.TimeoutExpired:
                st.sidebar.warning("Timeout — mēģini vēlāk")

    st.sidebar.markdown(
        '<div style="font-size:.65rem;color:#374151;text-align:center;margin-top:16px;">'
        'Vibecoder & Automator · 2026</div>',
        unsafe_allow_html=True,
    )

    return selected_fuel, loyalty_choice, amenity_filter


# ---------------------------------------------------------------------------
# Station card builder  (returns HTML string — rendered in a CSS Grid)
# ---------------------------------------------------------------------------

def build_card_html(
    station_id: str,
    station: dict,
    fuel: str,
    loyalty_key: str,
) -> str:
    """Return the complete HTML for one station card."""
    price       = station["prices"].get(fuel)
    loyalty     = LOYALTY_OPTIONS[loyalty_key]
    logo_b64    = _b64(station["logo"])
    trend_key   = station.get("trends", {}).get(fuel, "stable")
    trend_icon, trend_color = TREND_DATA.get(trend_key, ("→", "#9ca3af"))
    amenities_html = "".join(
        AMENITY_ICONS[a] for a in station.get("amenities", []) if a in AMENITY_ICONS
    )
    fresh     = _freshness(station.get("scraped_at", ""))
    has_error = bool(station.get("error"))

    # ── Logo ────────────────────────────────────────────────────────────────
    logo_tag = (
        f'<img src="{logo_b64}" class="station-logo" alt="{station["name"]}">'
        if logo_b64 else
        f'<span style="font-family:Syne,sans-serif;font-size:1.1rem;'
        f'font-weight:800;color:#00ff7f;">{station["name"]}</span>'
    )

    # ── Error badge ─────────────────────────────────────────────────────────
    error_html = ""
    if has_error:
        err_short = station["error"][:90] + ("…" if len(station["error"]) > 90 else "")
        error_html = f'<div class="error-badge">⚠ {err_short}</div>'

    # ── Price block ─────────────────────────────────────────────────────────
    discount_amount = (
        loyalty["discount"]
        if price is not None and loyalty["station"] == station_id and loyalty["discount"] > 0
        else 0.0
    )
    has_discount = discount_amount > 0

    if price is None:
        price_html    = '<div class="no-data">Cena nav pieejama</div>'
        discount_html = ""
        gift_html     = ""
    else:
        discounted = round(price - discount_amount, 3)
        price_html = (
            f'<div class="price-main">{price:.3f}'
            f'<span class="price-unit"> €/l</span>'
            f'<span class="trend-badge" style="color:{trend_color};">{trend_icon}</span>'
            f'</div>'
        )
        if has_discount:
            discount_html = (
                f'<div class="price-discount">{discounted:.3f} €/l</div>'
                f'<div class="gift-row">ar kartes atlaidi</div>'
            )
            gift_html = (
                f'<div class="gift-badge">'
                f'🎁 Izdevīgāk par {discount_amount:.2f}€ — {loyalty_key}'
                f'</div>'
            )
        else:
            discount_html = ""
            gift_html     = ""

    # ── Promos strip ────────────────────────────────────────────────────────
    promos_html = ""
    promos = [
        p for p in station.get("promos", [])
        if p.get("discount_eur", 0) > 0
        and (p.get("fuel") is None or p.get("fuel") == fuel)
    ]
    if promos:
        items = ""
        for p in promos[:2]:   # show max 2 promos per card
            fp = p.get("final_prices", {}).get(fuel)
            fp_tag = (
                f' → <span class="promo-final">{fp:.3f} €/l</span>'
                if fp else ""
            )
            items += (
                f'<div class="promo-item">'
                f'{p["promo_text"][:80]}{"…" if len(p["promo_text"]) > 80 else ""}'
                f'{fp_tag}</div>'
            )
        promos_html = f'<div class="promo-strip">{items}</div>'

    # ── Assemble ────────────────────────────────────────────────────────────
    card_class = "glass-card error" if has_error and not price else "glass-card"
    return (
        f'<div class="{card_class}">'
        f'  {logo_tag}'
        f'  <div class="station-name">{station["name"]}</div>'
        f'  <div style="font-size:.72rem;color:#6b7280;margin-bottom:10px;">'
        f'    {station.get("address", "")}'
        f'  </div>'
        f'  <hr style="border-color:rgba(255,255,255,.05);margin:8px 0;">'
        f'  {price_html}'
        f'  {discount_html}'
        f'  {gift_html}'
        f'  {promos_html}'
        f'  {error_html}'
        f'  <div class="amenities">{amenities_html}</div>'
        f'  <div class="freshness">{fresh}</div>'
        f'</div>'
    )


# ---------------------------------------------------------------------------
# Analytics block
# ---------------------------------------------------------------------------

def render_analytics(stations: dict, fuel: str, loyalty_key: str) -> None:
    st.markdown(
        '<hr style="border-color:rgba(255,255,255,.05);margin:8px 0 20px;">',
        unsafe_allow_html=True,
    )
    st.markdown(
        '<div class="section-header">// <span class="accent">Analītika</span></div>',
        unsafe_allow_html=True,
    )

    # Collect all available prices for selected fuel
    prices_with_ids = [
        (sid, s["prices"][fuel], s["name"])
        for sid, s in stations.items()
        if fuel in s.get("prices", {})
    ]

    if not prices_with_ids:
        st.info("Nav pieejamu cenu analītikai.")
        return

    prices_only = [p for _, p, _ in prices_with_ids]
    avg_price    = sum(prices_only) / len(prices_only)
    min_price    = min(prices_only)
    max_price    = max(prices_only)
    spread       = max_price - min_price

    # Metrics row
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("⌀ Vidējā cena", f"{avg_price:.3f} €/l")
    c2.metric("↓ Zemākā", f"{min_price:.3f} €/l")
    c3.metric("↑ Augstākā", f"{max_price:.3f} €/l")
    c4.metric("↔ Starpība", f"{spread:.3f} €/l")

    st.markdown("<br>", unsafe_allow_html=True)

    # Top-3 cheapest
    sorted_stations = sorted(prices_with_ids, key=lambda x: x[1])
    medals = ["🥇", "🥈", "🥉"]
    top_cols = st.columns(min(3, len(sorted_stations)))
    for i, (sid, price, name) in enumerate(sorted_stations[:3]):
        loyalty = LOYALTY_OPTIONS[loyalty_key]
        discount = loyalty["discount"] if loyalty["station"] == sid else 0.0
        net_price = round(price - discount, 3)
        logo_b64 = _b64(stations[sid]["logo"])
        logo_tag = (
            f'<img src="{logo_b64}" style="width:60px;height:30px;'
            f'object-fit:contain;margin-bottom:6px;">'
            if logo_b64 else ""
        )
        extra = (
            f'<div style="font-size:.75rem;color:#00ff7f;">'
            f'ar karti: {net_price:.3f} €/l</div>'
            if discount > 0 else ""
        )
        top_cols[i].markdown(
            f'<div class="analytic-box">'
            f'<div class="top-badge">{medals[i]} Top {i+1}</div><br>'
            f'{logo_tag}'
            f'<div class="analytic-value">{price:.3f} €/l</div>'
            f'<div class="analytic-label">{name}</div>'
            f'{extra}'
            f'</div>',
            unsafe_allow_html=True,
        )


# ---------------------------------------------------------------------------
# Main app
# ---------------------------------------------------------------------------

def main() -> None:
    inject_css()

    # ── Header ──────────────────────────────────────────────────────────────
    st.markdown(
        '<h1 style="font-family:Syne,sans-serif;font-size:2.2rem;'
        'font-weight:800;color:#fff;margin-bottom:2px;">'
        '⛽ Degvielas <span style="color:#00ff7f;">Cenas</span> Latvijā</h1>'
        '<p style="color:#6b7280;font-size:.85rem;margin-bottom:24px;">'
        'Reāllaika cenas no Circle K · Neste · Viada · Virši</p>',
        unsafe_allow_html=True,
    )

    # ── Sidebar ──────────────────────────────────────────────────────────────
    selected_fuel, loyalty_key, amenity_filter = render_sidebar()

    # ── Auto-bootstrap: generate demo data if prices.json is missing ─────────
    was_bootstrapped = bootstrap_if_needed()
    if was_bootstrapped:
        st.info(
            "**Demonstrācijas dati.** Cenas ir ilustratīvas. "
            "Nospied **🔄 Atjaunot cenas** sānjoslā, lai iegūtu reālās cenas."
        )

    # ── Load data ────────────────────────────────────────────────────────────
    data, load_error = load_data()

    if load_error:
        st.warning(load_error)
        return

    stations: dict = data.get("stations", {})

    if not stations:
        st.warning(
            "Datu fails ir tukšs. Nospied **🔄 Atjaunot cenas** sānjoslā "
            "vai palaid `python scraper.py --demo` terminālī."
        )
        return

    # ── Amenity filter ───────────────────────────────────────────────────────
    if amenity_filter:
        stations = {
            sid: s for sid, s in stations.items()
            if all(a in s.get("amenities", []) for a in amenity_filter)
        }
        if not stations:
            st.info("Nav AZS ar visiem izvēlētajiem pakalpojumiem.")
            return

    # ── Sort: cheapest first; stations with no price go to the end ───────────
    def sort_key(item: tuple) -> float:
        _, s = item
        p = s.get("prices", {}).get(selected_fuel)
        return p if p is not None else 9999.0

    sorted_stations = sorted(stations.items(), key=sort_key)

    # ── Section title ────────────────────────────────────────────────────────
    fuel_label = FUEL_LABELS.get(selected_fuel, selected_fuel)
    st.markdown(
        f'<div class="section-header">// Cenas — '
        f'<span class="accent">{fuel_label}</span></div>',
        unsafe_allow_html=True,
    )
    scraped_at = data.get("scraped_at", "")
    if scraped_at:
        try:
            ts = datetime.fromisoformat(scraped_at).strftime("%d.%m.%Y %H:%M")
        except Exception:
            ts = scraped_at
        # Show how many stations have a scrape error
        n_errors = sum(1 for s in stations.values() if s.get("error"))
        err_note  = (
            f' · <span style="color:#ff6b6b;">{n_errors} stacija(s) ar kļūdu</span>'
            if n_errors else ""
        )
        st.markdown(
            f'<p style="font-size:.72rem;color:#4b5563;margin-bottom:16px;">'
            f'Pēdējā atjaunošana: {ts}{err_note}</p>',
            unsafe_allow_html=True,
        )

    # ── Station cards — CSS Grid (auto-responsive) ───────────────────────────
    if not sorted_stations:
        st.info("Nav pieejamu staciju.")
        return

    cards_html = "\n".join(
        build_card_html(sid, s, selected_fuel, loyalty_key)
        for sid, s in sorted_stations
    )
    st.markdown(
        f'<div class="cards-grid">{cards_html}</div>',
        unsafe_allow_html=True,
    )

    # ── Analytics ────────────────────────────────────────────────────────────
    render_analytics(stations, selected_fuel, loyalty_key)


if __name__ == "__main__":
    main()
