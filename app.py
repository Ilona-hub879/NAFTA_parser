"""
Degvielas Cenas Latvijā — Streamlit dashboard
Visualises fuel prices scraped from Latvian gas station networks.
Design: DESIGN_SYSTEM.md (anthracite / emerald palette, Syne + Inter fonts).
"""

import base64
import json
import math
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from urllib import error as urlerror
from urllib import parse as urlparse
from urllib import request as urlrequest

import streamlit as st
import streamlit.components.v1 as components

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


def _freshness(iso: str) -> tuple[str, str]:
    """Return (human-readable age, css-color) based on data age."""
    try:
        delta = datetime.now() - datetime.fromisoformat(iso)
        mins  = int(delta.total_seconds() / 60)
        if mins < 1:
            return "tikko atjaunots", "#00ff7f"
        if mins < 60:
            return f"{mins} min. atpakaļ", "#00ff7f"
        hours = mins // 60
        if hours < 5:
            return f"{hours} h atpakaļ", "#facc15"
        return f"{hours} h atpakaļ", "#f87171"
    except Exception:
        return "nezināms laiks", "#9ca3af"


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


def _applicable_loyalties(station_id: str, selected_keys: list[str]) -> list[tuple[str, float]]:
    """Return all selected loyalty cards that apply to the station."""
    out: list[tuple[str, float]] = []
    for key in selected_keys:
        cfg = LOYALTY_OPTIONS.get(key)
        if not cfg:
            continue
        if cfg.get("station") == station_id and float(cfg.get("discount", 0)) > 0:
            out.append((key, float(cfg["discount"])))
    return out


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Distance between two points on Earth in kilometers."""
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (
        math.sin(dlat / 2) ** 2
        + math.cos(p1) * math.cos(p2) * math.sin(dlon / 2) ** 2
    )
    return r * (2 * math.atan2(math.sqrt(a), math.sqrt(1 - a)))


def _google_nearby_gas_stations(
    lat: float, lng: float, *, radius_m: int = 3500, limit: int = 10
) -> tuple[list[dict], str | None]:
    """
    Fetch nearby gas stations using Places API (New) — Nearby Search.
    POST https://places.googleapis.com/v1/places:searchNearby
    Required for projects created after June 2024.
    Returns (stations_list, error_message | None).
    """
    api_key = st.secrets.get("GOOGLE_PLACES_API_KEY")
    if not api_key:
        return [], (
            "Google Places API nav konfigurēts. "
            "Pievienojiet `GOOGLE_PLACES_API_KEY` Streamlit Secrets."
        )

    url  = "https://places.googleapis.com/v1/places:searchNearby"
    body = json.dumps({
        "includedTypes":     ["gas_station"],
        "maxResultCount":    min(limit, 20),
        "languageCode":      "lv",
        "locationRestriction": {
            "circle": {
                "center": {"latitude": lat, "longitude": lng},
                "radius": float(radius_m),
            }
        },
    }).encode("utf-8")

    # Field mask — only request the fields we actually use
    # userRatingCount is used to pick the "popular" name when the same address
    # appears multiple times under different place names.
    field_mask = (
        "places.displayName,places.location,places.formattedAddress,"
        "places.rating,places.userRatingCount"
    )

    req = urlrequest.Request(
        url,
        data=body,
        method="POST",
        headers={
            "Content-Type":     "application/json",
            "X-Goog-Api-Key":   str(api_key),
            "X-Goog-FieldMask": field_mask,
        },
    )

    try:
        with urlrequest.urlopen(req, timeout=12) as resp:
            payload = json.loads(resp.read().decode("utf-8", errors="replace"))
    except urlerror.HTTPError as exc:
        body_txt = exc.read().decode("utf-8", errors="replace")[:300]
        return [], f"Google Places HTTP {exc.code}: {body_txt}"
    except urlerror.URLError as exc:
        return [], f"Google Places savienojuma kļūda: {exc}"
    except Exception as exc:
        return [], f"Neizdevās nolasīt Google Places atbildi: {exc}"

    places = payload.get("places") or []

    def _norm_addr(addr: str) -> str:
        return " ".join(addr.lower().replace(",", " ").split())

    # De-duplicate by address:
    # keep the most "popular" entry (higher userRatingCount, then rating),
    # fallback tie-breaker: nearer distance.
    by_addr: dict[str, dict] = {}
    for p in places:
        loc   = p.get("location") or {}
        plat  = loc.get("latitude")
        plng  = loc.get("longitude")
        if plat is None or plng is None:
            continue
        name = (
            (p.get("displayName") or {}).get("text")
            or p.get("name")
            or "Nezināma DUS"
        )
        address = p.get("formattedAddress") or ""
        distance_km = round(_haversine_km(lat, lng, float(plat), float(plng)), 2)
        rating = p.get("rating")
        user_count = int(p.get("userRatingCount") or 0)
        item = {
            "name":        name,
            "address":     address,
            "distance_km": distance_km,
            "rating":      rating,
            "_user_count": user_count,
        }

        key = _norm_addr(address) if address else f"{name.lower()}::{plat:.6f},{plng:.6f}"
        existing = by_addr.get(key)
        if not existing:
            by_addr[key] = item
            continue

        existing_score = (
            int(existing.get("_user_count", 0)),
            float(existing.get("rating") or 0.0),
            -float(existing.get("distance_km") or 9999.0),
        )
        new_score = (
            user_count,
            float(rating or 0.0),
            -distance_km,
        )
        if new_score > existing_score:
            by_addr[key] = item

    out = [
        {k: v for k, v in item.items() if k != "_user_count"}
        for item in by_addr.values()
    ]
    out.sort(key=lambda x: x["distance_km"])
    return out[:limit], None


def render_nearby_stations_block() -> None:
    """
    Render nearest gas stations block.
    Uses streamlit-geolocation component (proper Streamlit bidirectional
    communication — no iframe sandbox restrictions).
    """
    from streamlit_geolocation import streamlit_geolocation  # lazy import

    st.markdown(
        '<div class="section-header">// Tuvākās <span class="accent">DUS</span></div>',
        unsafe_allow_html=True,
    )
    st.caption("Balstīts uz jūsu pārlūka atrašanās vietu · rādiuss: 3500 m.")

    location = streamlit_geolocation()

    # Not yet clicked / no data
    if not location or location.get("latitude") is None:
        if location is not None and location.get("latitude") is None:
            # Button was clicked but coords are null → permission denied
            st.warning("ieslēdziet ģeolokāciju")
        return

    lat = float(location["latitude"])
    lng = float(location["longitude"])

    with st.spinner("Meklēju tuvākās DUS..."):
        stations, api_error = _google_nearby_gas_stations(
            lat, lng, radius_m=3500, limit=10
        )

    if api_error:
        st.warning(api_error)
        return
    if not stations:
        st.info("Tuvumā DUS netika atrastas.")
        return

    for i, s in enumerate(stations, start=1):
        rating = f" · ⭐ {s['rating']}" if s.get("rating") else ""
        addr = s.get("address", "")
        maps_query = urlparse.quote_plus(addr or s["name"])
        maps_url = (
            "https://www.google.com/maps/dir/?api=1"
            f"&origin={lat:.6f},{lng:.6f}"
            f"&destination={maps_query}"
            "&travelmode=driving"
        )
        st.markdown(
            f"{i}. **{s['name']}** — {s['distance_km']:.2f} km{rating}<br>"
            f"<a href='{maps_url}' target='_blank' "
            f"style='color:#8ab4f8;text-decoration:underline;margin-right:10px;'>"
            f"{addr or 'Atvērt kartē'}</a>"
            f"<a href='{maps_url}' target='_blank' "
            f"style='display:inline-block;background:#0b5f31;color:#eafff2;"
            f"padding:2px 10px;border-radius:6px;text-decoration:none;font-size:.8rem;'>"
            f"🧭 Navigēt</a>",
            unsafe_allow_html=True,
        )


# ---------------------------------------------------------------------------
# CSS injection
# ---------------------------------------------------------------------------

def inject_css(light_mode: bool = False) -> None:
    bg_b64 = _b64("images/backgraund4.jpg")
    bg_layer = (
        f"url('{bg_b64}') center/cover fixed no-repeat"
        if bg_b64 else ("#f0f2f5" if light_mode else "#121212")
    )

    # ── Color tokens ──────────────────────────────────────────────────────────
    if light_mode:
        c_text        = "#111827"
        c_heading     = "#111827"
        c_app_bg      = "#f0f2f5"
        c_ov1         = "rgba(230,232,235,0.82)"
        c_ov2         = "rgba(220,222,225,0.65)"
        c_sidebar_bg  = "rgba(235,237,240,0.97)"
        c_sidebar_bdr = "rgba(0,0,0,0.07)"
        c_card_bg     = "rgba(242,244,247,0.93)"
        c_card_bdr    = "rgba(0,0,0,0.10)"
        c_card_shadow = "rgba(0,0,0,0.18)"
        c_card_hover  = "rgba(0,0,0,0.26)"
        c_radio_bg    = "rgba(0,0,0,0.04)"
        c_radio_bdr   = "rgba(0,0,0,0.08)"
        c_select_bg   = "rgba(0,0,0,0.03)"
        c_select_bdr  = "rgba(0,0,0,0.08)"
        c_btn_text    = "#111827"
        c_metric_bg   = "rgba(242,244,247,0.95)"
        c_metric_bdr  = "rgba(0,0,0,0.12)"
        c_metric_lbl  = "#374151"
        c_metric_val  = "#111827"
        c_hr          = "rgba(0,0,0,0.08)"
        c_alert_bg    = "rgba(242,244,247,0.95)"
        c_alert_bdr   = "rgba(0,0,0,0.15)"
        c_alert_text  = "#111827"
        c_gift_row    = "#6b7280"
        c_analytic_bg = "rgba(242,244,247,0.92)"
        c_analytic_bdr= "rgba(0,0,0,0.10)"
        c_promo_text  = "#4b5563"
        c_promo_bdr   = "rgba(0,0,0,.06)"
        c_no_data     = "#6b7280"
        c_scrollbar   = "#e5e7eb"
        c_scrollthumb = "rgba(0,120,60,.35)"
    else:
        c_text        = "#e5e7eb"
        c_heading     = "#ffffff"
        c_app_bg      = "#121212"
        c_ov1         = "rgba(15,23,42,0.92)"
        c_ov2         = "rgba(15,23,42,0.75)"
        c_sidebar_bg  = "rgba(18,18,18,0.97)"
        c_sidebar_bdr = "rgba(255,255,255,0.05)"
        c_card_bg     = "rgba(2,6,23,0.85)"
        c_card_bdr    = "rgba(250,204,21,0.35)"
        c_card_shadow = "rgba(0,0,0,0.70)"
        c_card_hover  = "rgba(0,0,0,0.85)"
        c_radio_bg    = "rgba(255,255,255,0.04)"
        c_radio_bdr   = "rgba(255,255,255,0.07)"
        c_select_bg   = "rgba(255,255,255,0.03)"
        c_select_bdr  = "rgba(255,255,255,0.07)"
        c_btn_text    = "#ffffff"
        c_metric_bg   = "rgba(2,6,23,0.85)"
        c_metric_bdr  = "rgba(250,204,21,0.35)"
        c_metric_lbl  = "#d1d5db"
        c_metric_val  = "#ffffff"
        c_hr          = "rgba(255,255,255,0.05)"
        c_alert_bg    = "rgba(2,6,23,0.85)"
        c_alert_bdr   = "rgba(250,204,21,0.35)"
        c_alert_text  = "#e5e7eb"
        c_gift_row    = "#9ca3af"
        c_analytic_bg = "rgba(2,6,23,0.85)"
        c_analytic_bdr= "rgba(250,204,21,0.35)"
        c_promo_text  = "#9ca3af"
        c_promo_bdr   = "rgba(255,255,255,.06)"
        c_no_data     = "#4b5563"
        c_scrollbar   = "#0a0a0a"
        c_scrollthumb = "rgba(0,255,127,.3)"

    st.markdown(f"""
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600&family=Syne:wght@600;700;800&family=Material+Symbols+Sharp:opsz,wght,FILL,GRAD@24,400,0,0&display=swap');

/* ── Base ── */
html, body, [class*="css"] {{
    font-family: 'Inter', sans-serif !important;
    color: {c_text};
}}
.stApp {{
    background:
        radial-gradient(circle at top left,  {c_ov1}, transparent 55%),
        radial-gradient(circle at bottom right, {c_ov2}, transparent 55%),
        {bg_layer};
    background-color: {c_app_bg};
}}
.block-container {{
    padding-top: 1.5rem !important;
    padding-bottom: 3rem !important;
    max-width: 1200px;
}}

/* ── Sidebar ── */
[data-testid="stSidebar"] {{
    background: {c_sidebar_bg} !important;
    border-right: 1px solid {c_sidebar_bdr} !important;
}}
[data-testid="stSidebar"] label,
[data-testid="stSidebar"] p,
[data-testid="stSidebar"] span {{
    color: {c_text} !important;
    font-family: 'Inter', sans-serif !important;
}}
/* Sidebar toggle: keep visible so JS can click it; just hide the raw icon text */
button[title*="keyboard"],
button[aria-label*="keyboard"] {{
    display: none !important;
}}
.material-symbols-sharp,
.material-symbols-outlined,
.material-symbols-rounded {{
    font-family: 'Material Symbols Sharp', 'Material Symbols Outlined', sans-serif !important;
    font-size: 22px !important;
    line-height: 1 !important;
    letter-spacing: normal !important;
    text-transform: none !important;
    white-space: nowrap !important;
    overflow: hidden !important;
    display: inline-block !important;
    max-width: 26px !important;
}}

/* ── Mobile: sidebar = full-screen overlay ── */
@media (max-width: 768px) {{
    section[data-testid="stSidebar"] {{
        width: 100vw !important;
        min-width: 100vw !important;
        max-width: 100vw !important;
        position: fixed !important;
        top: 0 !important;
        left: 0 !important;
        height: 100dvh !important;
        z-index: 999999 !important;
        overflow-y: auto !important;
    }}
    section[data-testid="stSidebar"] > div:first-child {{
        width: 100vw !important;
        min-width: 100vw !important;
        padding: 1rem 1.2rem !important;
    }}
}}

/* ── Headings ── */
h1, h2, h3 {{
    font-family: 'Syne', sans-serif !important;
    color: {c_heading} !important;
}}

/* ── Buttons ── */
.stButton > button {{
    background: rgba(0,255,127,0.10) !important;
    border: 1px solid #00ff7f !important;
    color: {c_btn_text} !important;
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
    background: {c_radio_bg};
    border: 1px solid {c_radio_bdr};
    border-radius: 8px;
    padding: 6px 14px;
    color: {c_text} !important;
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
    background: {c_select_bg} !important;
    border: 1px solid {c_select_bdr} !important;
    border-radius: 12px !important;
    color: {c_text} !important;
}}
[data-baseweb="tag"] {{
    background: rgba(0,255,127,0.15) !important;
    border: 1px solid #00ff7f !important;
    color: #00ff7f !important;
}}

/* ── Metric ── */
[data-testid="metric-container"] {{
    background: {c_metric_bg};
    backdrop-filter: blur(14px);
    border: 1px solid {c_metric_bdr};
    border-radius: 16px;
    padding: 18px 20px;
}}
[data-testid="metric-container"] label {{
    color: {c_metric_lbl} !important;
    font-size: 1rem !important;
    text-transform: uppercase;
    letter-spacing: .08em;
}}
[data-testid="stMetricValue"] {{
    color: {c_metric_val} !important;
    font-family: 'Inter', sans-serif !important;
    font-size: 1.8rem !important;
    font-weight: 700 !important;
    font-variant-numeric: tabular-nums !important;
    letter-spacing: -.01em !important;
}}
[data-testid="stMetricDelta"] {{
    font-size: .8rem !important;
}}

/* ── Divider ── */
hr {{
    border-color: {c_hr} !important;
}}

/* ── Alert/info ── */
.stAlert {{
    background: {c_alert_bg} !important;
    border: 1px solid {c_alert_bdr} !important;
    color: {c_alert_text} !important;
    border-radius: 12px !important;
}}

/* ── Checkbox ── */
.stCheckbox label {{ color: {c_text} !important; }}

/* ── Custom cards ── */
.glass-card {{
    background: {c_card_bg};
    backdrop-filter: blur(14px);
    -webkit-backdrop-filter: blur(14px);
    border: 1px solid {c_card_bdr};
    border-radius: 18px;
    box-shadow: 0 18px 45px {c_card_shadow};
    padding: 22px 24px;
    margin-bottom: 14px;
    transition: transform .3s ease, box-shadow .3s ease;
}}
.glass-card:hover {{
    transform: translateY(-5px);
    box-shadow: 0 24px 55px {c_card_hover};
}}
.station-logo {{
    width: 80px;
    height: 40px;
    object-fit: contain;
    margin-bottom: 8px;
}}
.station-name {{
    font-family: 'Syne', sans-serif;
    font-size: 1.55rem;
    font-weight: 700;
    color: {c_heading};
    margin: 4px 0;
}}
.price-main {{
    font-family: 'Syne', sans-serif;
    font-size: 1.5rem;
    font-weight: 700;
    color: {c_heading};
    line-height: 1;
}}
.price-unit {{
    font-size: 1rem;
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
    font-size: 1.05rem;
    color: {c_gift_row};
    margin-top: 2px;
}}
.gift-badge {{
    display: inline-block;
    background: rgba(0,255,127,0.12);
    border: 1px solid rgba(0,255,127,0.35);
    border-radius: 8px;
    padding: 5px 12px;
    font-size: 1rem;
    color: #00ff7f;
    margin-top: 6px;
}}
.trend-badge {{
    font-size: 1.1rem;
    font-weight: 700;
    margin-left: 6px;
}}
.freshness {{
    font-size: .95rem;
    color: #4b5563;
    letter-spacing: .04em;
    margin-top: 10px;
}}
.amenities {{
    font-size: 1.3rem;
    margin-top: 8px;
    letter-spacing: .04em;
}}
.section-header {{
    font-family: 'Syne', sans-serif;
    font-size: 1.4rem;
    font-weight: 700;
    color: {c_heading};
    margin-bottom: 4px;
}}
.section-header .accent {{
    color: #00ff7f;
}}
.analytic-box {{
    background: {c_analytic_bg};
    backdrop-filter: blur(14px);
    border: 1px solid {c_analytic_bdr};
    border-radius: 16px;
    padding: 20px;
    text-align: center;
}}
.analytic-value {{
    font-family: 'Inter', sans-serif;
    font-size: 1.9rem;
    font-weight: 700;
    color: #00ff7f;
    font-variant-numeric: tabular-nums;
    letter-spacing: -.01em;
}}
.analytic-label {{
    font-size: 1rem;
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
    color: {c_no_data};
    font-size: 1.1rem;
    font-style: italic;
}}
.adus-note {{
    font-size: .78rem;
    color: #6b7280;
    font-style: italic;
    margin-top: 3px;
    padding: 3px 0;
}}

/* ── Responsive CSS Grid for station cards ── */
.cards-grid {{
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(260px, 1fr));
    gap: 18px;
    margin-bottom: 28px;
}}
@media (min-width: 1400px) {{
    .cards-grid {{ grid-template-columns: repeat(4, 1fr); }}
}}
@media (min-width: 900px) and (max-width: 1399px) {{
    .cards-grid {{ grid-template-columns: repeat(3, 1fr); }}
}}
@media (min-width: 580px) and (max-width: 899px) {{
    .cards-grid {{ grid-template-columns: repeat(2, 1fr); }}
}}
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
    padding: 6px 12px;
    font-size: .95rem;
    color: #ff6b6b;
    margin-top: 8px;
    word-break: break-word;
}}

/* ── Promo card strip ── */
.promo-strip {{
    margin-top: 10px;
    border-top: 1px solid {c_promo_bdr};
    padding-top: 8px;
}}
.promo-item {{
    font-size: 1rem;
    color: {c_promo_text};
    line-height: 1.5;
    margin-bottom: 5px;
    padding-left: 8px;
    border-left: 2px solid rgba(0,255,127,.4);
}}
.promo-final {{
    font-family: 'Syne', sans-serif;
    font-size: 1.2rem;
    font-weight: 700;
    color: #00ff7f;
}}

/* ── Scrollbar ── */
::-webkit-scrollbar {{ width: 5px; }}
::-webkit-scrollbar-track {{ background: {c_scrollbar}; }}
::-webkit-scrollbar-thumb {{ background: {c_scrollthumb}; border-radius: 3px; }}

/* ── Hide default chrome ── */
#MainMenu, footer {{ visibility: hidden; }}
</style>
""", unsafe_allow_html=True)


def inject_js() -> None:
    """
    Execute JavaScript that modifies the Streamlit DOM.
    Must use components.html() — script tags in st.markdown are stripped.
    window.parent.document gives access to the main Streamlit frame.
    """
    components.html(
        """<script>
(function patchUI() {
    function patchArrows() {
        var doc = window.parent.document;
        doc.querySelectorAll('button span, button p, button').forEach(function(el) {
            var t = (el.textContent || '').trim();
            if (t === 'keyboard_double_arrow_left' || t === 'keyboard_double_arrow_right') {
                var arrow = (t === 'keyboard_double_arrow_left') ? '<<' : '>>';
                el.textContent = arrow;
                el.style.cssText =
                    'font-family:Syne,sans-serif;font-size:1.2rem;' +
                    'font-weight:700;color:#00ff7f;letter-spacing:normal;' +
                    'display:inline;visibility:visible;';
                // Also ensure the parent button is visible
                var btn = el.closest('button') || el;
                if (btn.tagName === 'BUTTON') {
                    btn.style.visibility = 'visible';
                    btn.style.display = 'flex';
                }
            }
        });
    }

    function patchGeoBtn() {
        var doc = window.parent.document;
        doc.querySelectorAll('iframe').forEach(function(fr) {
            try {
                if (!fr.src || fr.src.indexOf('streamlit_geolocation') === -1) return;
                var fdoc = fr.contentDocument || fr.contentWindow.document;
                if (!fdoc) return;
                var btn = fdoc.querySelector('button');
                if (!btn || btn.dataset.geoStyled === '1') return;
                btn.textContent = '🎯 ieslēdziet ģeolokāciju šeit';
                btn.style.cssText = [
                    'width:100%',
                    'padding:10px 14px',
                    'border-radius:10px',
                    'border:1px solid #00ff7f',
                    'background:linear-gradient(180deg,#0f5132,#0a3f27)',
                    'color:#eafff2',
                    'font-weight:700',
                    'font-family:Inter,sans-serif',
                    'cursor:pointer',
                    'font-size:1rem',
                    'box-shadow:0 8px 24px rgba(0,0,0,.25)',
                    'display:block'
                ].join(';');
                btn.dataset.geoStyled = '1';
                fr.style.cssText = 'width:100%;min-height:52px;display:block;border:none;';
            } catch(e) {}
        });
    }

    function run() {
        patchArrows();
        patchGeoBtn();
    }
    run();
    setInterval(run, 500);
})();
</script>""",
        height=0,
    )


# ---------------------------------------------------------------------------
# Fuel-selector sync helpers
# ---------------------------------------------------------------------------

def _sync_main_from_sb() -> None:
    """Sidebar fuel radio changed → keep main-screen selector in sync."""
    st.session_state["main_fuel"] = st.session_state.get("sb_fuel")


def _sync_sb_from_main() -> None:
    """Main-screen fuel radio changed → keep sidebar selector in sync."""
    st.session_state["sb_fuel"] = st.session_state.get("main_fuel")


# ---------------------------------------------------------------------------
# Sidebar
# ---------------------------------------------------------------------------

def render_sidebar() -> tuple[str, list[str], list[str]]:
    """Returns (selected_fuel, loyalty_cards, amenity_filter)."""

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
        'letter-spacing:.12em;margin-bottom:12px;">Cenas reāllaikā</div>'
        '<div style="font-family:\'Inter\',sans-serif;font-size:1rem;'
        'color:#9ca3af;margin-bottom:16px;line-height:1.5;">'
        '👇 Atlasiet vajadzīgās opcijas</div>',
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
        "fuel_type", fuel_names,
        key="sb_fuel",
        on_change=_sync_main_from_sb,
        label_visibility="collapsed",
    )
    selected_fuel = fuel_keys[fuel_names.index(fuel_choice)]

    st.sidebar.markdown("---")

    # Loyalty cards (multi-select)
    st.sidebar.markdown(
        '<p style="font-size:.72rem;color:#6b7280;text-transform:uppercase;'
        'letter-spacing:.1em;margin-bottom:4px;">🎁 Lojalitātes karte</p>',
        unsafe_allow_html=True,
    )
    loyalty_keys = [k for k in LOYALTY_OPTIONS.keys() if k != "Nav kartes"]
    loyalty_choice = st.sidebar.multiselect(
        "loyalty",
        loyalty_keys,
        key="sb_loyalty",
        label_visibility="collapsed",
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
        with st.spinner("Iegūst cenas…"):
            ok, err = _run_scraper([])
        if ok:
            st.sidebar.success("✅ Dati atjaunoti!")
            st.session_state["_close_sidebar"] = True
            st.rerun()
        else:
            # Real scraper failed — silently fall back to demo data
            with st.spinner("Reālie dati nav pieejami — ģenerē demonstrācijas datus…"):
                ok_demo, _ = _run_scraper(["--demo"])
            if ok_demo:
                st.sidebar.warning(
                    "⚠️ Reālie dati nav pieejami. Rādīti demonstrācijas dati."
                )
                st.session_state["_close_sidebar"] = True
                st.rerun()
            else:
                st.sidebar.error(
                    "Neizdevās iegūt datus. Pārbaudiet interneta savienojumu."
                )

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
    loyalty_keys: list[str],
) -> str:
    """Return the complete HTML for one station card."""
    lm          = st.session_state.get("light_mode", False)
    hr_color    = "rgba(0,0,0,.08)" if lm else "rgba(255,255,255,.05)"
    addr_color  = "#374151" if lm else "#6b7280"
    price       = station["prices"].get(fuel)
    logo_b64    = _b64(station["logo"])
    trend_key   = station.get("trends", {}).get(fuel, "stable")
    trend_icon, trend_color = TREND_DATA.get(trend_key, ("→", "#9ca3af"))
    amenities_html = "".join(
        AMENITY_ICONS[a] for a in station.get("amenities", []) if a in AMENITY_ICONS
    )
    fresh, _  = _freshness(station.get("scraped_at", ""))
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
    card_discounts = _applicable_loyalties(station_id, loyalty_keys)
    has_discount = len(card_discounts) > 0

    # ADUS secondary-price note (Viada only)
    adus_note_html = ""
    if station_id == "viada":
        adus_price = station.get("adus_prices", {}).get(fuel)
        if adus_price is not None and price is not None and round(adus_price, 3) != round(price, 3):
            adus_note_html = (
                f'<div class="adus-note">'
                f'ADUS (bez personāla): {adus_price:.3f} €/l'
                f'</div>'
            )

    if price is None:
        price_html    = '<div class="no-data">Cena nav pieejama</div>'
        discount_html = ""
        gift_html     = ""
    else:
        price_html = (
            f'<div class="price-main">{price:.3f}'
            f'<span class="price-unit"> €/l</span>'
            f'<span class="trend-badge" style="color:{trend_color};">{trend_icon}</span>'
            f'</div>'
            f'{adus_note_html}'
        )
        if has_discount:
            discount_rows = []
            gift_rows = []
            for card_name, discount_amount in card_discounts:
                discounted = round(price - discount_amount, 3)
                discount_rows.append(
                    f'<div class="price-discount">{discounted:.3f} €/l '
                    f'<span style="font-size:.8rem;color:#9ca3af;">({card_name})</span></div>'
                )
                gift_rows.append(
                    f'<div class="gift-badge">🎁 -{discount_amount:.3f} €/l — {card_name}</div>'
                )
            discount_html = "".join(discount_rows) + '<div class="gift-row">ar izvēlētajām kartēm</div>'
            gift_html = "".join(gift_rows)
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
        f'  <div style="font-size:.72rem;color:{addr_color};margin-bottom:10px;">'
        f'    {station.get("address", "")}'
        f'  </div>'
        f'  <hr style="border-color:{hr_color};margin:8px 0;">'
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



# ---------------------------------------------------------------------------
# Main app
# ---------------------------------------------------------------------------

def main() -> None:
    light_mode = st.session_state.get("light_mode", False)
    inject_css(light_mode)
    inject_js()

    # ── Auto-open sidebar on very first visit (mobile + any browser) ─────────
    if "_welcomed" not in st.session_state:
        st.session_state["_welcomed"] = True
        components.html(
            """<script>
(function openSidebar() {
    var tries = 0;
    var t = setInterval(function() {
        tries++;
        if (tries > 60) { clearInterval(t); return; }
        var doc = window.parent.document;
        var sidebar = doc.querySelector('section[data-testid="stSidebar"]');
        if (!sidebar) return;

        // Already open: sidebar wider than 60 px
        var rect = sidebar.getBoundingClientRect();
        if (rect.width > 60) { clearInterval(t); return; }

        // 1. Try every known toggle-button selector (order matters)
        var selectors = [
            '[data-testid="stSidebarCollapsedControl"] button',
            '[data-testid="collapsedControl"] button',
            '[data-testid="collapsedControl"]',
            'button[data-testid="stBaseButton-headerNoPadding"]',
            'button[aria-label*="sidebar"]',
            'button[aria-label*="Sidebar"]',
            'button[aria-label*="menu"]',
            'button[aria-label*="Menu"]',
            'header button',
        ];
        for (var i = 0; i < selectors.length; i++) {
            var btn = doc.querySelector(selectors[i]);
            if (btn && btn.offsetParent !== null) {
                btn.click();
                clearInterval(t);
                return;
            }
        }

        // 2. Fallback: force sidebar visible via style overrides
        sidebar.style.setProperty('transform', 'none', 'important');
        sidebar.style.setProperty('left', '0', 'important');
        sidebar.style.setProperty('display', 'block', 'important');
        sidebar.style.setProperty('visibility', 'visible', 'important');
        clearInterval(t);
    }, 150);
})();
</script>""",
            height=0,
        )

    # ── Auto-close sidebar after scrape refresh ──────────────────────────────
    if st.session_state.pop("_close_sidebar", False):
        components.html(
            """<script>
(function tryClose() {
    var attempts = 0;
    var t = setInterval(function() {
        attempts++;
        if (attempts > 25) { clearInterval(t); return; }
        var doc = window.parent.document;
        var sidebar = doc.querySelector('section[data-testid="stSidebar"]');
        if (!sidebar) return;
        var btn = doc.querySelector('[data-testid="collapsedControl"]')
                  || sidebar.querySelector('button[data-testid="baseButton-header"]')
                  || sidebar.querySelector('button[kind="header"]')
                  || sidebar.querySelector('button');
        if (btn) { btn.click(); clearInterval(t); }
    }, 200);
})();
</script>""",
            height=0,
        )

    # ── Header ──────────────────────────────────────────────────────────────
    _hcol, _tcol = st.columns([11, 1])
    heading_color = "#111827" if light_mode else "#ffffff"
    sub_color     = "#374151" if light_mode else "#6b7280"
    with _hcol:
        st.markdown(
            f'<h1 style="font-family:Syne,sans-serif;font-size:2.2rem;'
            f'font-weight:800;color:{heading_color};margin-bottom:2px;">'
            f'⛽ Degvielas <span style="color:#00ff7f;">Cenas</span> Latvijā</h1>'
            f'<p style="color:{sub_color};font-size:.85rem;margin-bottom:24px;">'
            f'Reāllaika cenas no Circle K · Neste · Viada · Virši</p>',
            unsafe_allow_html=True,
        )
    with _tcol:
        toggle_icon = "☀️" if not light_mode else "🌙"
        toggle_help = "Pārslēgt uz gaišo tēmu" if not light_mode else "Pārslēgt uz tumšo tēmu"
        if st.button(toggle_icon, key="theme_toggle", help=toggle_help):
            # Don't call st.rerun() — the button click already triggers a rerun.
            # Calling it early would interrupt render_sidebar() and orphan widget keys.
            st.session_state["light_mode"] = not light_mode

    # ── Sidebar ──────────────────────────────────────────────────────────────
    selected_fuel, loyalty_keys, amenity_filter = render_sidebar()

    # Ensure main_fuel key exists and stays in sync with sidebar on first render
    if "main_fuel" not in st.session_state:
        st.session_state["main_fuel"] = st.session_state.get(
            "sb_fuel", list(FUEL_LABELS.values())[0]
        )

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

    # ── Inline fuel selector (main screen) ──────────────────────────────────
    st.markdown(
        '<div class="section-header">// <span class="accent">Cenas</span></div>',
        unsafe_allow_html=True,
    )
    st.radio(
        "Degvielas veids",
        list(FUEL_LABELS.values()),
        key="main_fuel",
        on_change=_sync_sb_from_main,
        horizontal=True,
        label_visibility="collapsed",
    )
    # Derive selected_fuel from main-screen selector (kept in sync with sidebar)
    selected_fuel = next(
        (k for k, v in FUEL_LABELS.items() if v == st.session_state["main_fuel"]),
        "95",
    )

    # ── Sort: cheapest first; stations with no price go to the end ───────────
    def sort_key(item: tuple) -> float:
        _, s = item
        p = s.get("prices", {}).get(selected_fuel)
        return p if p is not None else 9999.0

    sorted_stations = sorted(stations.items(), key=sort_key)
    scraped_at = data.get("scraped_at", "")
    if scraped_at:
        try:
            ts = datetime.fromisoformat(scraped_at).strftime("%d.%m.%Y %H:%M")
        except Exception:
            ts = scraped_at
        age_text, age_color = _freshness(scraped_at)
        n_errors  = sum(1 for s in stations.values() if s.get("error"))
        err_badge = (
            f'<span style="background:#3f1515;color:#f87171;border-radius:6px;'
            f'padding:2px 10px;font-size:.8rem;margin-left:8px;">'
            f'⚠ {n_errors} kļūda</span>'
            if n_errors else ""
        )
        _ts_text  = "#374151" if light_mode else "#6b7280"
        _ts_val   = "#111827" if light_mode else "#e5e7eb"
        _ts_bg    = "rgba(0,0,0,.06)" if light_mode else "rgba(255,255,255,.06)"
        _ts_faint = "#374151" if light_mode else "#4b5563"
        st.markdown(
            f'<div style="display:flex;align-items:center;gap:8px;'
            f'margin-bottom:18px;flex-wrap:wrap;">'
            f'  <span style="font-size:.85rem;color:{_ts_text};">🕐 Cenas atjaunotas:</span>'
            f'  <span style="font-size:.9rem;color:{_ts_val};font-weight:600;">{ts}</span>'
            f'  <span style="font-size:.8rem;color:{age_color};'
            f'  background:{_ts_bg};border-radius:6px;padding:2px 10px;">'
            f'  {age_text}</span>'
            f'  {err_badge}'
            f'  <span style="font-size:.75rem;color:{_ts_faint};margin-left:4px;">'
            f'  · atjaunojas automātiski ik pēc 4 h</span>'
            f'</div>',
            unsafe_allow_html=True,
        )

    # ── Station cards — CSS Grid (auto-responsive) ───────────────────────────
    if not sorted_stations:
        st.info("Nav pieejamu staciju.")
        return

    cards_html = "\n".join(
        build_card_html(sid, s, selected_fuel, loyalty_keys)
        for sid, s in sorted_stations
    )
    st.markdown(
        f'<div class="cards-grid">{cards_html}</div>',
        unsafe_allow_html=True,
    )

    render_nearby_stations_block()


if __name__ == "__main__":
    main()
