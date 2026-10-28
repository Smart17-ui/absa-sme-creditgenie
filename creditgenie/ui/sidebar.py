"""Sidebar: brand, the role switch, and a summary of what is open."""
import html

import streamlit as st

from creditgenie.applications import sme_application, start_new_application
from creditgenie.config import LOGO_URI, ROLES
from creditgenie.ui.analyst import ai_recommendation
from creditgenie.utils import pretty_time

ROLE_LABELS = {"SME Portal": ":material/storefront: Business owner",
               "Absa Analyst": ":material/badge: Absa analyst"}


def side_rows(rows):
    return "".join(f'<div class="cg-row"><span>{k}</span><span>{v}</span></div>' for k, v in rows)


def render_role_switch():
    """A demo stand-in for login: each role sees only its own view."""
    with st.sidebar:
        st.html(f'<div class="cg-side-brand"><img class="cg-logo" src="{LOGO_URI}" alt="Absa"><div>'
                '<div class="cg-side-name">Credit<span>Genie</span></div>'
                '<div class="cg-side-tag">Absa · AI-powered SME lending</div></div></div>')
        role = st.segmented_control(":material/lock: Signed in as", ROLES, default=ROLES[0], required=True,
                                    key="role", width="stretch", format_func=ROLE_LABELS.get,
                                    help="Simulated login for the demo. In production, business owners and "
                                         "Absa analysts sign in separately and only see their own view.")
        # Re-assigning keeps the name when the field is hidden (Streamlit drops values of unseen widgets)
        st.session_state.analyst_name = st.session_state.get("analyst_name", "Credit analyst 1")
        if role == "Absa Analyst":
            st.text_input("Your name", key="analyst_name", help="Signs your decisions in the audit trail. "
                          "Large loans and overrides need a second analyst with a different name.")
        st.caption(":material/verified_user: Simulated secure login · demo only")
    return role


def render_summary(role):
    """Rendered last so it always reflects the latest state."""
    applications = st.session_state.applications
    with st.sidebar:
        application = sme_application()

        if role == "SME Portal":
            if application:
                st.html('<div class="cg-side-card"><div class="cg-side-label">Your application</div>' + side_rows([
                    ("Reference", application["ref"]),
                    ("Amount", f"ZMW {application['requested_zmw']:,.0f}"),
                    ("Status", application["status"]),
                    ("Documents sent", str(len(application["evidence"]))),
                ]) + "</div>")
                st.button("Start new application", on_click=start_new_application, icon=":material/restart_alt:",
                          width="stretch", help="Your earlier applications stay listed on this page.")
            else:
                st.html('<div class="cg-side-card"><div class="cg-side-label">How it works</div>' + side_rows([
                    ("1", "Verify your number"),
                    ("2", "Tell us about your business"),
                    ("3", "Share your records"),
                    ("4", "Apply - we reply here"),
                ]) + "</div>")
        elif applications.get(st.session_state.get("analyst_ref")):
            selected = applications[st.session_state.analyst_ref]
            case, report = selected["case"], selected["report"]
            st.html('<div class="cg-side-card"><div class="cg-side-label">Current case</div>' + side_rows([
                ("Case", case["case_id"]),
                ("Applicant", html.escape(selected["business_name"])),
                ("Owner", html.escape(selected["profile"]["owner_name"])),
                ("Status", selected["status"]),
                ("Evidence", f"{len(case['processed'])} analysed"),
                ("Assessments", str(len(case["history"]))),
            ]) + "</div>")
            if report:
                st.html('<div class="cg-side-card"><div class="cg-side-label">Latest assessment</div>' + side_rows([
                    ("AI recommends", ai_recommendation(report["result"])[0]),
                    ("Score", f"{report['result']['credit_score']}/100"),
                    ("Loan", f"ZMW {report['result']['recommended_loan_zmw']:,.0f}"),
                    ("When", pretty_time(report["generated_at"])),
                    ("Audit ID", report["audit_id"]),
                ]) + "</div>")
                st.caption("Compliance checks are in the **Audit trail** tab.")

        st.html('<div class="cg-side-foot">Aligned with Zambia\'s Data Protection Act No. 3 of 2021 and POPIA. '
                f'Session started {pretty_time(st.session_state.session_started)}.</div>')
