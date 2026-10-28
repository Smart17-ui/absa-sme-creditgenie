"""Building blocks shared by the SME Portal and the Analyst Workbench."""
import html

import streamlit as st

from creditgenie.applications import delete_application, restore_application, trash_application
from creditgenie.config import LOGO_URI
from creditgenie.utils import pretty_time


def hero(role_label, title, tagline, chips, stats=(), analyst=False):
    stats_html = ("" if not stats else '<div class="cg-hero-stats">' + "".join(
        f'<div class="cg-hero-stat"><b title="{value}">{value}</b><span>{label}</span></div>'
        for label, value in stats) + "</div>")
    chips_html = ('<div class="cg-chips">' + "".join(f'<span class="cg-chip">{c}</span>' for c in chips) + "</div>"
                  if chips else "")
    st.html(f'<div class="cg-hero{" cg-analyst" if analyst else ""}"><div class="cg-hero-main">'
            f'<div class="cg-hero-top"><img class="cg-hero-logo" src="{LOGO_URI}" alt="Absa">'
            f'<div class="cg-hero-role"><span class="cg-dot"></span>{role_label}</div></div>'
            f'<h1>{title}</h1><p>{tagline}</p>{chips_html}</div>{stats_html}</div>')


def step_header(number, title, subtitle):
    st.html(f'<div class="cg-step"><span class="cg-step-num">{number}</span>{title}</div>'
            f'<div class="cg-sub">{subtitle}</div>')


def ai_insight(text, footnote):
    st.html(f'<div class="cg-ai"><div class="cg-ai-head"><span class="cg-ai-spark">&#10022;</span>'
            f'Generative AI insight</div><div class="cg-ai-body">{html.escape(text)}</div>'
            f'<div class="cg-ai-foot">{footnote}</div></div>')


STATUS_BADGE = {
    "Submitted": ("blue", ":material/send:"),
    "Under review": ("orange", ":material/manage_search:"),
    "More info requested": ("orange", ":material/help:"),
    "Awaiting second approval": ("violet", ":material/group:"),
    "Approved": ("green", ":material/check_circle:"),
    "Declined": ("red", ":material/cancel:"),
}


def status_badge(status):
    color, icon = STATUS_BADGE[status]
    st.badge(status, icon=icon, color=color)


def profile_summary(app):
    """Who owns the business and what it does, as given in the application."""
    p, consent = app["profile"], app["consent"]

    def rows(pairs):
        return "  \n".join(f":gray[{label}:] {html.escape(str(value))}" for label, value in pairs if value not in ("", None))

    owner_col, business_col = st.columns(2, gap="large")
    owner_col.markdown("**:material/person: Business owner**  \n" + rows([
        ("Name", p["owner_name"]),
        ("Position", p["owner_role"]),
        ("Mobile", f"{consent['phone_masked']} · {consent['network']} · verified {pretty_time(consent['verified_at'])}"),
        ("Email", p["owner_email"]),
    ]))
    business_col.markdown("**:material/storefront: Business**  \n" + rows([
        ("Name", app["business_name"]),
        ("Sector", p["sector"]),
        ("Location", f"{p['town']}, {p['province']} Province"),
        ("Trading for", f"{p['years_trading']} year(s) · {p['employees']} employee(s)"),
        ("PACRA", p["pacra_number"] or "Not registered"),
    ]))
    st.markdown(f":gray[What the business does:] {html.escape(p['description'])}")


def ask_delete(ref, viewer):
    st.session_state.confirm_delete = (ref, viewer)


def cancel_delete():
    st.session_state.pop("confirm_delete", None)


def confirm_delete(ref, viewer):
    cancel_delete()
    delete_application(ref, viewer)


def application_rows(apps, viewer, current_ref, on_open, empty_text, trash=False):
    """One row per application, with Open / Trash, or Restore / Delete when showing the trash."""
    if not apps:
        st.caption(empty_text)
        return
    for a in apps:
        ref = a["ref"]
        details = [
            html.escape(a["profile"]["owner_name"]) if viewer == "analyst" else None,
            f"{a['profile']['sector']}, {html.escape(a['profile']['town'])}" if viewer == "analyst" else None,
            f"ZMW {a['requested_zmw']:,.0f} for {a['purpose'].lower()}",
            f"AI score {a['report']['result']['credit_score']}/100" if viewer == "analyst" and a["report"] else None,
            f"in trash since {pretty_time(a['trashed'][viewer])}" if trash else f"submitted {pretty_time(a['submitted_at'])}",
        ]
        info_col, badge_col, action_col = st.columns([5, 2, 2], vertical_alignment="center")
        info_col.markdown(f"**{ref}** · {html.escape(a['business_name'])}  \n"
                          f":gray[{' · '.join(d for d in details if d)}]")
        with badge_col:
            status_badge(a["status"])
        with action_col.container(horizontal=True, horizontal_alignment="right"):
            if trash:
                st.button("Restore", key=f"{viewer}_restore_{ref}", icon=":material/restore_from_trash:",
                          on_click=restore_application, args=(ref, viewer), type="tertiary")
                st.button("Delete", key=f"{viewer}_delete_{ref}", icon=":material/delete_forever:",
                          on_click=ask_delete, args=(ref, viewer), type="tertiary")
            else:
                if ref == current_ref:
                    st.badge("Open", icon=":material/visibility:", color="violet")
                else:
                    st.button("Open", key=f"{viewer}_open_{ref}", icon=":material/open_in_new:",
                              on_click=on_open, args=(ref,), type="tertiary")
                st.button("Trash", key=f"{viewer}_trash_{ref}", icon=":material/delete:",
                          on_click=trash_application, args=(ref, viewer), type="tertiary",
                          help="Move to trash - you can restore it later")
        if trash and st.session_state.get("confirm_delete") == (ref, viewer):
            other = "analyst" if viewer == "sme" else "sme"
            if a["deleted"][other]:
                note = "Nobody else has a copy, so the application and its documents are erased completely."
            elif viewer == "sme":
                note = "Absa keeps its own copy for its credit records."
            else:
                note = "The customer's own copy in the SME Portal is not affected."
            with st.container(border=True, horizontal=True, vertical_alignment="center"):
                st.markdown(f":red[:material/warning:] **Delete {ref} for good?** This can't be undone. :gray[{note}]")
                st.button("Cancel", key=f"{viewer}_cancel_{ref}", on_click=cancel_delete)
                st.button("Delete permanently", key=f"{viewer}_confirm_{ref}", type="primary",
                          icon=":material/delete_forever:", on_click=confirm_delete, args=(ref, viewer))
