"""RobotOps — synthetic robotics operations dashboard (Streamlit entry point)."""

from pathlib import Path

import streamlit as st

from views import dashboard, maintenance_page, ui

st.set_page_config(page_title="RobotOps", page_icon=Path(__file__).parent / "static" / "wrench.svg", layout="wide")

pages = [
    st.Page(dashboard.render, title="Fleet dashboard", icon=":material/dashboard:", url_path="fleet", default=True),
    st.Page(maintenance_page.render, title="Inspections & maintenance", icon=":material/handyman:", url_path="maintenance"),
]
nav = st.navigation(pages)

with st.sidebar:
    st.markdown("### :material/build: RobotOps")
    st.caption("San Francisco Lab · synthetic fleet, no hardware connected")
    api = ui.api()
    health = ui.act(api.health)
    if health and health["data_mode"] == "demo":
        st.info("Demo sandbox: your edits are private to this browser session and expire after inactivity.", icon=":material/lock:")
    elif health:
        st.info("Shared mode: edits persist for all users.", icon=":material/database:")
    url, embedded = ui.api_endpoint()
    if embedded:
        st.caption(":material/api: REST API running embedded in this app")
    else:
        st.caption(f":material/api: REST API: `{url}` · [docs]({url}/docs)")
    with st.popover("Reset data", icon=":material/restart_alt:", width="stretch"):
        st.write("Restore the simulated dataset. All your changes will be lost.")
        if st.button("Yes, reset", type="primary", key="reset_confirm"):
            if ui.act(api.reset):  # POST /api/v1/session/reset
                ui.flash("success", "Data reset to a fresh simulated dataset.")
                st.rerun()

ui.show_flashes()
nav.run()
