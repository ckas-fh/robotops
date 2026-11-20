import pandas as pd
import streamlit as st

from robotops import clock
from robotops.constants import INSPECTION_KINDS, INSPECTION_RESULTS, TECHNICIANS, TICKET_PRIORITIES, TICKET_STATUSES

from . import ui


def render():
    ui.header("Inspections & maintenance", "Track repair tickets and keep every robot's inspections on schedule.")
    api = ui.api()
    robots = pd.DataFrame(ui.load(api.fleet_status)["robots"])  # GET /api/v1/fleet/status
    tickets = pd.DataFrame(ui.load(api.issues))  # GET /api/v1/issues
    robots["next_inspection"] = pd.to_datetime(robots["next_inspection"]).dt.date
    names = list(robots["name"])
    insp = robots["inspection_state"]

    ui.metrics([
        ("Open tickets", len(tickets), "unresolved", "Tickets that are New, In progress, or Waiting on parts."),
        ("Urgent tickets", int(tickets["priority"].isin(["Critical", "High"]).sum()) if len(tickets) else 0, "Critical or High",
         "Critical tickets take a robot Down; High tickets put it in Maintenance."),
        ("Inspections overdue", int((insp == "Overdue").sum()), "robots", "Past their next inspection date."),
        ("Inspections due soon", int((insp == "Due soon").sum()), "within 3 days", "Next inspection is due in the next 3 days."),
    ])

    # --- Tickets --------------------------------------------------------------
    st.subheader("Open tickets")
    f1, f2, f3 = st.columns([1, 1, 1.4])
    priorities = f1.multiselect("Priority", TICKET_PRIORITIES, placeholder="All priorities")
    statuses = f2.multiselect("Status", TICKET_STATUSES[:-1], placeholder="All statuses")
    search = f3.text_input("Search", placeholder="Robot name or issue", key="ticket_search")
    view = tickets
    if len(view):
        if priorities:
            view = view[view["priority"].isin(priorities)]
        if statuses:
            view = view[view["status"].isin(statuses)]
        if search:
            s = search.strip()
            view = view[view["robot"].str.contains(s, case=False, regex=False) | view["title"].str.contains(s, case=False, regex=False)]
        st.dataframe(
            ui.styled(view[["id", "robot", "title", "priority", "status", "assignee", "opened_on", "github_issue_url"]], "priority"),
            hide_index=True, width="stretch",
            column_config={"id": "#", "robot": "Robot", "title": "Issue", "priority": "Priority", "status": "Status",
                           "assignee": "Assignee", "opened_on": "Opened",
                           "github_issue_url": st.column_config.LinkColumn("GitHub", display_text="Open issue")},
        )
    st.caption(f"Showing {len(view)} of {len(tickets)} open tickets.")

    # --- Inspection schedule ------------------------------------------------------
    st.subheader("Inspection schedule")
    sched = robots.sort_values("days_to_inspection")[["name", "model", "last_inspection", "last_result", "next_inspection", "inspection_state"]]
    st.dataframe(
        ui.styled(sched, "last_result", "inspection_state"), hide_index=True, width="stretch",
        column_config={**ui.ROBOT_COLUMNS, "last_inspection": "Last inspection", "last_result": "Last result",
                       "inspection_state": "Schedule"},
    )

    # --- Actions (same expander pattern as the fleet dashboard) ---------------------
    with st.expander("Log an inspection", icon=":material/fact_check:"):
        with st.form("inspection", clear_on_submit=True):
            a, b = st.columns(2)
            robot = a.selectbox("Robot", names)
            inspector = b.selectbox("Inspector", TECHNICIANS)
            kind = a.selectbox("Type", INSPECTION_KINDS)
            when = b.date_input("Date", value=clock.now().date(), max_value=clock.now().date())
            result = st.radio("Result", INSPECTION_RESULTS, horizontal=True)
            notes = st.text_input("Notes")
            st.caption("A Fail opens a High ticket and moves the robot to Maintenance. Needs attention opens a Medium ticket.")
            if st.form_submit_button("Log inspection", type="primary"):
                res = ui.act(api.log_inspection, robot, inspector, kind, result, notes, when)  # POST /api/v1/inspections
                if res:
                    ui.flash("success", f"Inspection logged for {robot}.")
                    for m in res["messages"]:
                        ui.flash("warning", m)
                    st.rerun()

    with st.expander("Open a ticket", icon=":material/add:"):
        with st.form("ticket", clear_on_submit=True):
            a, b = st.columns(2)
            robot_t = a.selectbox("Robot", names, key="t_robot")
            assignee = b.selectbox("Assignee", TECHNICIANS, key="t_assignee")
            title = st.text_input("Issue", placeholder="e.g. Gripper not closing fully")
            priority = st.select_slider("Priority", TICKET_PRIORITIES, value="Medium")
            st.caption("Critical takes the robot Down.")
            if st.form_submit_button("Open ticket", type="primary"):
                res = ui.act(api.create_issue, robot_t, title, priority, assignee)  # POST /api/v1/issues
                if res:
                    ui.flash("success", f"Ticket #{res['id']} opened for {robot_t}.")
                    st.rerun()

    if not len(tickets):
        return
    options = {f"#{r.id} · {r.robot} · {r.title}": r for r in tickets.itertuples()}

    with st.expander("Update a ticket", icon=":material/edit:"):
        with st.form("update_ticket"):
            t = options[st.selectbox("Ticket", list(options))]
            a, b = st.columns(2)
            status = a.selectbox("Status", TICKET_STATUSES)
            assignee = b.selectbox("Assignee", TECHNICIANS)
            note = st.text_input("Resolution note", placeholder="Required when closing")
            st.caption("Closing a robot's last Critical/High ticket returns it to Operational.")
            if st.form_submit_button("Save", type="primary"):
                res = ui.act(api.update_issue, int(t.id), status, assignee, note)  # PATCH /api/v1/issues/{id}
                if res:
                    ui.flash("success", f"Ticket #{t.id} {'closed' if status == 'Closed' else 'updated'}.")
                    for m in res["messages"]:
                        ui.flash("info", m)
                    st.rerun()

    with st.expander("Send a ticket to GitHub Issues", icon=":material/publish:"):
        _github_section(api, tickets)


def _github_section(api, tickets: pd.DataFrame):
    cfg = ui.load(api.github_config)  # GET /api/v1/integrations/github
    live = cfg["mode"] == "live"
    if live:
        st.warning(f"**Live mode**: approving will create a real issue in `{cfg['repo']}`.", icon=":material/warning:")
    else:
        st.info(f"**Dry-run mode** ({cfg['dry_run_reason']}): you'll see exactly what would be sent, but nothing leaves this app.",
                icon=":material/science:")

    unlinked = tickets[tickets["github_issue_url"].isna()]
    if unlinked.empty:
        st.write("Every open ticket is already linked to a GitHub issue.")
        return
    labels = {f"#{r.id} · {r.robot} · {r.title}": int(r.id) for r in unlinked.itertuples()}
    tid = labels[st.selectbox("Ticket", list(labels), key="gh_ticket")]

    preview = ui.act(api.github_preview, tid)  # GET /api/v1/issues/{id}/github/preview
    if not preview:
        return
    draft = preview["draft"]
    with st.container(border=True):
        st.markdown(f"**{draft['title']}**")
        st.caption("Labels: " + ", ".join(f"`{l}`" for l in draft["labels"]) + (f" · Repository: `{cfg['repo']}`" if cfg["repo"] else ""))
        st.markdown(draft["body"])

    target = f"`{cfg['repo']}`" if live else "GitHub (dry run, nothing will be sent)"
    approved = st.checkbox(f"I reviewed this issue and approve creating it in {target}.", key=f"gh_approve_{tid}")
    if st.button("Create GitHub issue" if live else "Run dry run", type="primary", disabled=not approved,
                 icon=":material/check:"):
        res = ui.act(api.github_create, tid, approved=True)  # POST /api/v1/issues/{id}/github
        if res and res["dry_run"]:
            st.success("Dry run complete. The payload above is exactly what would be sent; no request was made to GitHub.",
                       icon=":material/science:")
        elif res:
            st.session_state.pop(f"gh_approve_{tid}", None)
            ui.flash("success", f"Created GitHub issue #{res['number']}: {res['url']}")
            st.rerun()
