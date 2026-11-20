import altair as alt
import pandas as pd
import streamlit as st

from robotops.constants import ROBOT_STATUSES

from . import ui

TELEOP_WINDOWS = {"Past 7 days": "past", "Today": "today", "Next 7 days": "upcoming"}


def render():
    ui.header("Fleet dashboard", "Live view of the simulated robot fleet at San Francisco Lab.")
    api = ui.api()
    fleet = ui.load(api.fleet_status)  # GET /api/v1/fleet/status
    k = fleet["kpis"]
    robots = pd.DataFrame(fleet["robots"])
    robots["next_inspection"] = pd.to_datetime(robots["next_inspection"]).dt.date

    ui.metrics([
        ("Available", f"{k['available']}/{k['fleet_size']}", f"{k['availability_pct']:.0f}%",
         "Robots that can be used right now: Operational or Charging."),
        ("Down", k["down"], None, "Broken and unusable until a Critical ticket is resolved."),
        ("In maintenance", k["in_maintenance"], None, "Taken out of service while a technician works on them."),
        ("Open tickets", k["open_tickets"], "unresolved", "Tickets that are New, In progress, or Waiting on parts."),
    ])

    st.subheader("Status by model")
    by_model = robots.groupby(["model", "status"]).size().reset_index(name="robots")
    st.altair_chart(ui.bar_chart(by_model, "robots", "model", color="status", color_map=ui.STATUS_COLORS, sort=None, height=alt.Step(40)), width="stretch")

    st.subheader("Robots")
    f1, f2, f3 = st.columns([1, 1, 1.4])
    statuses = f1.multiselect("Status", ROBOT_STATUSES, placeholder="All statuses")
    models = f2.multiselect("Model", sorted(robots["model"].unique()), placeholder="All models")
    search = f3.text_input("Search", placeholder="Robot name, e.g. HRN-003")
    view = robots
    if statuses:
        view = view[view["status"].isin(statuses)]
    if models:
        view = view[view["model"].isin(models)]
    if search:
        view = view[view["name"].str.contains(search.strip(), case=False, regex=False)]

    table = view[["name", "model", "status", "battery_pct", "health_score", "open_tickets", "next_inspection", "firmware", "uptime_hours"]]
    st.dataframe(
        ui.styled(table, "status"), hide_index=True, width="stretch",
        column_config={
            **ui.ROBOT_COLUMNS,
            "battery_pct": st.column_config.ProgressColumn("Battery", format="%d%%", min_value=0, max_value=100),
            "health_score": st.column_config.ProgressColumn("Health", format="%d", min_value=0, max_value=100,
                                                            help="Overall condition score from 0 to 100, based on diagnostics."),
            "open_tickets": st.column_config.NumberColumn("Open tickets", help="Unresolved maintenance tickets for this robot."),
            "firmware": "Firmware",
            "uptime_hours": st.column_config.NumberColumn("Operating hours", format="%.0f",
                                                          help="Total hours the robot has run since it was commissioned."),
        },
    )
    st.caption(f"Showing {len(view)} of {len(robots)} robots.")

    with st.expander("Change a robot's status", icon=":material/edit:"):
        with st.form("status_form"):
            a, b = st.columns(2)
            name = a.selectbox("Robot", robots["name"])
            status = b.selectbox("New status", ROBOT_STATUSES)
            if st.form_submit_button("Update status", type="primary"):
                if ui.act(api.set_robot_status, name, status):  # PATCH /api/v1/robots/{name}/status
                    ui.flash("success", f"{name} set to {status}.")
                    st.rerun()

    _teleop_section(api)


def _teleop_section(api):
    st.subheader("Teleoperation sessions")
    label = st.segmented_control("Window", list(TELEOP_WINDOWS), default="Next 7 days", label_visibility="collapsed") or "Next 7 days"
    data = ui.load(api.teleop_sessions, TELEOP_WINDOWS[label])  # GET /api/v1/teleop/sessions
    sessions = pd.DataFrame(data["sessions"])
    if sessions.empty:
        st.info("No teleoperation sessions in this window.", icon=":material/event_busy:")
        return
    sessions["start_at"] = pd.to_datetime(sessions["start_at"])
    sessions["end_at"] = pd.to_datetime(sessions["end_at"])
    st.dataframe(
        ui.styled(sessions[["robot", "task", "operator", "start_at", "end_at", "target_episodes", "collected_episodes", "status"]], "status"),
        hide_index=True, width="stretch",
        column_config={
            "robot": "Robot", "task": "Task", "operator": "Operator", "status": "Status",
            "start_at": st.column_config.DatetimeColumn("Start", format="ddd D MMM, HH:mm"),
            "end_at": st.column_config.DatetimeColumn("End", format="HH:mm"),
            "target_episodes": st.column_config.NumberColumn("Target episodes", help="Demonstrations the operator aims to record."),
            "collected_episodes": st.column_config.NumberColumn("Collected", help="Episodes actually recorded (completed sessions)."),
        },
    )
    st.caption(f"{data['count']} session(s) · simulated data collection where operators remotely drive robots to record training demonstrations.")
