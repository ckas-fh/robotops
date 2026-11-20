"""Shared Streamlit helpers: API access, styling, small components."""

from __future__ import annotations

import os
import uuid

import altair as alt
import streamlit as st

from robotops.client import ApiError, RobotOpsClient

# Cool-toned robot status palette; validated for color-blind separation and contrast (dataviz validator, light mode).
STATUS_COLORS = {"Operational": "#008300", "Charging": "#2a78d6", "Maintenance": "#0f9d8a", "Down": "#4a3aa7"}
# Text colors for state cells in tables (paired with the label text, never color alone).
STATE_COLORS = {
    **STATUS_COLORS,  # robot status text matches the chart
    "OK": "#0a7d0a",
    "Due soon": "#b26b00", "Overdue": "#d03b3b", "Never inspected": "#898781",
    "Pass": "#0a7d0a", "Needs attention": "#b26b00", "Fail": "#d03b3b",
    "Critical": "#d03b3b", "High": "#b26b00",
    "Scheduled": "#2a78d6", "In progress": "#0a7d0a", "Completed": "#898781", "Cancelled": "#898781",
}


def styled(df, *cols, colors: dict | None = None):
    """Color the given state columns' text with a leading dot; returns a Styler for st.dataframe."""
    palette = {**STATE_COLORS, **(colors or {})}
    df = df.copy()
    for c in cols:
        df[c] = df[c].map(lambda v: f"● {v}" if palette.get(v) else v)

    def color(v):
        key = v[2:] if isinstance(v, str) and v.startswith("● ") else v
        return f"color: {palette[key]}; font-weight: 500" if palette.get(key) else ""

    return df.style.map(color, subset=list(cols))

CSS = """
<style>
  .block-container {padding-top: 2rem; max-width: 1300px;}
  [data-testid="stMetric"] {background: var(--ro-card); border: 1px solid var(--ro-border);
      border-radius: 12px; padding: 14px 16px;}
  [data-testid="stMetricLabel"] p {font-size: .8rem; text-transform: uppercase; letter-spacing: .04em; opacity: .75;}
  .ro-hero {padding: 4px 0 12px;}
  .ro-hero h1 {font-size: 1.9rem; margin: 0;}
  .ro-hero p {margin: 2px 0 0; opacity: .7;}
  .ro-pill {display:inline-block; padding:2px 10px; border-radius:999px; font-size:.78rem; font-weight:600;
      border:1px solid var(--ro-border); background: var(--ro-card);}
  :root {--ro-card: rgba(11,11,11,.025); --ro-border: rgba(11,11,11,.10);}
  @media (prefers-color-scheme: dark) {:root {--ro-card: rgba(255,255,255,.04); --ro-border: rgba(255,255,255,.12);}}
</style>
"""


def session_id() -> str:
    if "sid" not in st.session_state:
        st.session_state.sid = uuid.uuid4().hex
    return st.session_state.sid


@st.cache_resource(show_spinner="Starting the RobotOps API…")
def api_endpoint() -> tuple[str, bool]:
    """(base_url, embedded?). Uses ROBOTOPS_API_URL, else starts the API in-process on localhost."""
    url = os.environ.get("ROBOTOPS_API_URL", "").strip()
    if url:
        return url, False
    from api.embedded import embedded_api_url

    return embedded_api_url(), True


def api() -> RobotOpsClient:
    """Per-visitor API client; the visitor's session id travels in the X-RobotOps-Session header."""
    if "api_client" not in st.session_state:
        st.session_state.api_client = RobotOpsClient(
            api_endpoint()[0], session_id(), api_key=os.environ.get("ROBOTOPS_API_KEY") or None)
    return st.session_state.api_client


def load(fn, *args, **kwargs):
    """Fetch data for a page; on API failure show the error with a retry button and stop rendering."""
    try:
        return fn(*args, **kwargs)
    except ApiError as e:
        st.error(f"Couldn't load data: {e}", icon=":material/cloud_off:")
        st.button("Retry", icon=":material/refresh:")
        st.stop()


def act(fn, *args, **kwargs):
    """Run a write call; show the API's error inline and return None on failure."""
    try:
        return fn(*args, **kwargs)
    except ApiError as e:
        st.error(str(e), icon=":material/error:")
        return None


def header(title: str, subtitle: str) -> None:
    st.markdown(CSS, unsafe_allow_html=True)
    st.markdown(f'<div class="ro-hero"><h1>{title}</h1><p>{subtitle}</p></div>', unsafe_allow_html=True)


def flash(kind: str, msg: str) -> None:
    """Queue a message to show after st.rerun()."""
    st.session_state.setdefault("_flash", []).append((kind, msg))


def show_flashes() -> None:
    for kind, msg in st.session_state.pop("_flash", []):
        getattr(st, kind)(msg)


def bar_chart(df, x: str, y: str, color: str | None = None, color_map: dict | None = None, horizontal=True, height=220, sort="-x"):
    """Thin, rounded, tooltip-enabled Altair bar chart with recessive axes."""
    enc_x, enc_y = (alt.X(x, title=None, axis=alt.Axis(grid=True, tickMinStep=1)), alt.Y(y, title=None, sort=sort)) if horizontal else (
        alt.X(y, title=None, sort=sort), alt.Y(x, title=None, axis=alt.Axis(grid=True, tickMinStep=1)))
    chart = alt.Chart(df).mark_bar(cornerRadiusEnd=4, size=18 if horizontal else None).encode(
        x=enc_x, y=enc_y, tooltip=list(df.columns))
    if color:
        scale = alt.Scale(domain=list(color_map), range=list(color_map.values())) if color_map else alt.Undefined
        chart = chart.encode(color=alt.Color(color, scale=scale, legend=alt.Legend(orient="top", title=None)))
    return chart.properties(height=height).configure_view(strokeWidth=0).configure_axis(
        gridColor="#e1e0d9", gridOpacity=0.5, domainColor="#c3c2b7", labelColor="#898781", labelLimit=220)


# Shared column labels so both pages name robot fields the same way.
ROBOT_COLUMNS = {
    "name": "Robot",
    "model": "Model",
    "status": "Status",
    "next_inspection": st.column_config.DateColumn(
        "Next inspection", help="Date the robot's next routine inspection is due (last inspection + its interval)."),
}


def metrics(cards: list[tuple]) -> None:
    """Render a row of KPI cards: (label, value, caption, help)."""
    for col, (label, value, caption, help_text) in zip(st.columns(len(cards)), cards):
        col.metric(label, value, caption, help=help_text, delta_color="off", delta_arrow="off", height="stretch")
