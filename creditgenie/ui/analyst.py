"""VIEW 2: ABSA ANALYST - the AI prepares the assessment, the analyst decides."""
import html
import json

import altair as alt
import pandas as pd
import streamlit as st
from google.genai import errors as genai_errors
from pydantic import ValidationError

from creditgenie.analysis import AIBusyError, run_analysis
from creditgenie.applications import save_applications
from creditgenie.config import DECIDED, MAX_IMAGES_PER_RUN, MODEL_NAME, PERSONAS
from creditgenie.feeds import evidence_class
from creditgenie.fraud import fraud_signals
from creditgenie.policy import (FOUR_EYES_LIMIT_ZMW, LOAN_TERMS, MAX_REPAYMENT_SHARE, MIN_ACTIVE_MONTHS,
                                MIN_HISTORY_DAYS, MIN_LOAN_ZMW, repayment_plan)
from creditgenie.ui.components import ai_insight, application_rows, hero, profile_summary, status_badge
from creditgenie.utils import flash, now_iso, pretty_time


INCOME_COLOR, EXPENSE_COLOR, DEBT_COLOR = "#2a78d6", "#eb6834", "#1baf7a"
BAND_COLORS = {"Low risk": "#1F8A4C", "Medium risk": "#C98500", "High risk": "#C62828"}
PERSONA_CAPTIONS = (
    "Conservative: declines when debt is high",
    "Growth-focused: rewards healthy cash flow",
    "Privacy-first: anonymises names before scoring",
)


def credit_band(score):
    if score >= 70:
        return "Low risk"
    if score >= 40:
        return "Medium risk"
    return "High risk"


def ai_recommendation(res):
    if res["recommended_loan_zmw"] <= 0:
        return "Decline", ":material/cancel:", "red"
    if res["suspicious_documents"]:
        return "Refer - check documents", ":material/gpp_maybe:", "orange"
    if res["credit_score"] >= 70:
        return "Approve", ":material/check_circle:", "green"
    return "Approve with conditions", ":material/info:", "orange"


def kpi_tiles(report):
    res = report["result"]
    history = res["trading_history"]
    score = res["credit_score"]
    band = credit_band(score)
    previous = report["previous_assessments"]

    score_note = f"{band} · Absa scorecard" if res.get("score_factors") else band
    if previous:
        change = score - previous[-1]["credit_score"]
        score_note += f" · {change:+d} vs last" if change else " · unchanged vs last"
    plan = res.get("repayment_plan")
    if res["decline_reasons"]:
        loan_note = "Blocked by Absa policy"
    elif plan:
        loan_note = f"ZMW {plan['monthly_instalment']:,.0f}/month for {plan['term_months']} months"
    else:
        loan_note = "Within Absa policy"
    period = f"{history['months']} months" if history["dated_transactions"] >= 2 else "period unknown"

    def money(value):
        return f'<span class="cg-cur">ZMW</span>{value:,.0f}'

    meter = (f'<div class="cg-meter"><div class="cg-meter-fill" style="width:{score}%;background:{BAND_COLORS[band]}"></div>'
             '<div class="cg-meter-tick" style="left:40%"></div><div class="cg-meter-tick" style="left:70%"></div></div>')
    tiles = [
        ("Credit score", f'{score}<span class="cg-unit">/100</span>', score_note, meter),
        ("Recommended loan", money(res["recommended_loan_zmw"]),
         f"{loan_note} · asked ZMW {report['application']['requested_zmw']:,.0f}", ""),
        ("Net cash flow", money(res["net_cash_flow"]), f"Income minus expenses · {period}", ""),
        ("Outstanding debt", money(res["outstanding_debt"]), "Owed, not yet paid", ""),
        ("Verified income", f'{res["verified_income_pct"]}<span class="cg-unit">%</span>',
         "Backed by MTN / Airtel records", ""),
        ("Verified history", f'{history["months"]}<span class="cg-unit">months</span>',
         f"Sales in {history['active_months']} month(s) · "
         + ("meets the 6-month rule" if history["span_days"] >= MIN_HISTORY_DAYS
            and history["active_months"] >= MIN_ACTIVE_MONTHS else "needs 6 months"), ""),
    ]
    st.html('<div class="cg-kpis">' + "".join(
        f'<div class="cg-kpi"><div class="cg-kpi-label">{label}</div><div class="cg-kpi-value">{value}</div>'
        f'{extra}<div class="cg-kpi-note">{note}</div></div>'
        for label, value, note, extra in tiles
    ) + "</div>")


def score_breakdown(res):
    """Where every point of the score came from - the same records always give the same score."""
    factors = res.get("score_factors")
    if not factors:
        return  # assessment made before the scorecard existed
    st.markdown(f"**How the score of {res['credit_score']} was calculated**")
    st.dataframe(
        pd.DataFrame([{"Factor": f["factor"], "Points": f"{f['points']} / {f['max_points']}",
                       "Achieved": 100 * f["points"] / f["max_points"], "Why": f["detail"]} for f in factors]),
        hide_index=True,
        width="stretch",
        column_config={
            "Achieved": st.column_config.ProgressColumn(min_value=0, max_value=100, format="%.0f%%", width="small"),
            "Why": st.column_config.TextColumn(width="large"),
        },
    )
    st.caption(f"Absa's fixed scorecard, calculated from the records - not by the AI. The AI's own estimate was "
               f"{res['ai_proposed_credit_score']}/100 (second opinion only).")


def repayment_summary(res):
    plan, afford = res.get("repayment_plan"), res.get("affordability")
    if not afford:
        return
    if plan:
        st.markdown(f"**Repayment plan** · ZMW {plan['amount_zmw']:,.0f} over {plan['term_months']} months  \n"
                    f"ZMW **{plan['monthly_instalment']:,.0f}/month** · ZMW {plan['total_repayable']:,.0f} in total "
                    f"· {plan['annual_rate']:.0%} a year (illustrative)")
    st.caption(f"Affordability: verified free cash flow is ZMW {afford['verified_monthly_free_cash']:,.0f}/month, so "
               f"repayments can be up to ZMW {afford['max_monthly_instalment']:,.0f}/month "
               f"({MAX_REPAYMENT_SHARE:.0%}). Over {afford['term_months']} months that supports a loan of up to "
               f"ZMW {afford['max_affordable_loan']:,.0f}.")


def record_decision(ref):
    app = st.session_state.applications[ref]
    report = app["report"]
    # Widget keys are per assessment, so a re-assessment starts from fresh, pre-filled values
    suffix = report["audit_id"]
    outcome = st.session_state[f"decision_outcome_{suffix}"]
    note = st.session_state[f"decision_note_{suffix}"].strip()
    amount = float(st.session_state[f"decision_amount_{suffix}"]) if outcome == "Approve" else 0.0
    term = st.session_state[f"decision_term_{suffix}"] if outcome == "Approve" else None
    analyst = st.session_state.get("analyst_name", "").strip()
    recommended = report["result"]["recommended_loan_zmw"]
    override = (outcome == "Approve" and amount > recommended) or (outcome == "Decline" and recommended > 0)

    if not analyst:
        flash("error", "Enter your name in the sidebar - every decision is signed.")
        return
    if outcome == "Approve" and amount < MIN_LOAN_ZMW:
        flash("error", f"Enter a loan amount of at least ZMW {MIN_LOAN_ZMW:,}, or choose Decline.")
        return
    reasons = report["result"]["decline_reasons"] if outcome != "Approve" else []
    # The policy reasons already tell the customer why; otherwise the analyst must explain
    if outcome != "Approve" and not note and not reasons:
        flash("error", "Add a note explaining the decision - the customer will see it.")
        return
    if override and not note:
        flash("error", "This overrides the scorecard and policy recommendation. Add a justification note.")
        return

    needs_second = outcome == "Approve" and (amount > FOUR_EYES_LIMIT_ZMW or override)
    decision = {
        "outcome": outcome,
        "amount_zmw": amount,
        "offer": repayment_plan(amount, term) if outcome == "Approve" else None,
        "note": note,
        "override_of_recommendation": override,
        "decided_at": now_iso(),
        "analyst": analyst,
        "based_on_audit_id": report["audit_id"],
        "reasons": reasons,
        "needs_second_approval": needs_second,
        "second_approval": None,
    }
    report["analyst_decision"] = decision
    app["decision"] = decision
    app["decisions"].append({"event": "Decision", **decision})
    if needs_second:
        app["status"] = "Awaiting second approval"
    else:
        app["status"] = {"Approve": "Approved", "Decline": "Declined"}.get(outcome, "More info requested")
    app["updated_at"] = decision["decided_at"]
    save_applications()
    st.session_state.toast = ("Sent for second approval - another analyst must confirm it." if needs_second
                              else f"Decision recorded: {app['status']}. The SME Portal now shows it.")


def second_approval(ref, confirm):
    """Four-eyes check: a different analyst confirms or rejects an approval."""
    app = st.session_state.applications[ref]
    decision = app["decision"]
    approver = st.session_state.get(f"second_approver_{decision['based_on_audit_id']}", "").strip()
    if not approver:
        flash("error", "Enter the second approver's name.")
        return
    if approver.lower() == decision["analyst"].lower():
        flash("error", f"{decision['analyst']} made this decision, so a different analyst must approve it.")
        return
    decision["second_approval"] = {"by": approver, "at": now_iso(), "outcome": "Confirmed" if confirm else "Rejected"}
    app["decisions"].append({"event": "Second approval", "based_on_audit_id": decision["based_on_audit_id"],
                             **decision["second_approval"]})
    if confirm:
        app["status"] = "Approved"
        st.session_state.toast = f"Approval confirmed by {approver}. The SME Portal now shows the offer."
    else:
        # Back to the first analyst: the assessment stays, the decision has to be made again
        app["status"] = "Under review"
        app["report"]["analyst_decision"] = None
        app["decision"] = None
        st.session_state.toast = f"{approver} rejected the approval - the application is back under review."
    app["updated_at"] = decision["second_approval"]["at"]
    save_applications()


def select_for_review(ref):
    st.session_state.analyst_ref = ref


def analyst_dashboard(flash_msg):
    applications = st.session_state.applications
    mine = [a for a in applications.values() if not a["deleted"]["analyst"]]
    active = [a for a in mine if not a["trashed"]["analyst"]]
    waiting = [a for a in active if a["status"] not in DECIDED]  # oldest first: first come, first served
    handled = [a for a in reversed(active) if a["status"] in DECIDED]  # most recent first
    approved = [a for a in handled if a["status"] == "Approved"]
    declined = [a for a in handled if a["status"] == "Declined"]
    trashed = [a for a in reversed(mine) if a["trashed"]["analyst"]]

    hero("Absa credit analyst · workbench", "AI credit <em>decisioning workbench</em>",
         "The AI agent prepares the credit assessment from the SME's shared data. You make the final decision.",
         [f"Model · {html.escape(MODEL_NAME)}", "PII masked before AI", "Policy-guarded",
          "Human decision & audit trail"],
         stats=[("Awaiting decision", str(len(waiting))), ("Handled", str(len(handled))),
                ("In trash", str(len(trashed)))],
         analyst=True)

    if not mine:
        with st.container(border=True, key="cg_card_empty"):
            st.markdown("#### :material/inbox: No applications waiting")
            st.caption("Switch to **SME Portal** in the sidebar, verify a business, share data and submit an "
                       "application. It will appear here.")
        return

    if st.session_state.get("analyst_ref") not in {a["ref"] for a in active}:
        st.session_state.analyst_ref = waiting[0]["ref"] if waiting else None

    # --- QUEUE ---
    with st.container(border=True, key="cg_card_queue"):
        st.markdown("#### :material/inbox: Applications")
        waiting_tab, approved_tab, declined_tab, trash_tab = st.tabs([
            f":material/hourglass_top: Awaiting decision ({len(waiting)})",
            f":material/check_circle: Approved ({len(approved)})",
            f":material/cancel: Declined ({len(declined)})",
            f":material/delete: Trash ({len(trashed)})",
        ])
        with waiting_tab:
            st.caption("Oldest first. Includes applications waiting for a second approval or for more "
                       "documents from the customer.")
            application_rows(waiting, "analyst", st.session_state.analyst_ref, select_for_review,
                             "Nothing waiting - every application has a decision.")
        with approved_tab:
            st.caption("Most recent first. Open one to see the offer, whether the customer accepted it, "
                       "and the audit trail.")
            application_rows(approved, "analyst", st.session_state.analyst_ref, select_for_review,
                             "No approvals yet.")
        with declined_tab:
            st.caption("Most recent first. Open one to see the reasons given to the customer and the audit trail.")
            application_rows(declined, "analyst", st.session_state.analyst_ref, select_for_review,
                             "No declines yet.")
        with trash_tab:
            st.caption("Trash only hides an application from the workbench - the customer still sees it. "
                       "Restore it, or delete it from the workbench for good.")
            application_rows(trashed, "analyst", None, None, "Trash is empty.", trash=True)

    if st.session_state.analyst_ref is None:
        st.info("Open an application above to review it.", icon=":material/arrow_upward:")
        return

    app = applications[st.session_state.analyst_ref]
    case = app["case"]
    new_items = [e for e in app["evidence"] if e["fp"] not in case["processed"]]

    # --- APPLICATION ---
    with st.container(border=True, key="cg_card_application"):
        head_col, badge_col = st.columns([3, 1], vertical_alignment="center")
        head_col.markdown(f"#### {html.escape(app['business_name'])}")
        head_col.caption(f"Application {app['ref']} · ZMW {app['requested_zmw']:,.0f} for {app['purpose'].lower()} · "
                         f"submitted {pretty_time(app['submitted_at'])}")
        if app["linked_accounts"]:
            head_col.caption("Linked accounts: " + " · ".join(
                f"{link['provider']} {link['phone_masked']}" for link in app["linked_accounts"].values()))
        with badge_col.container(horizontal=True, horizontal_alignment="right"):
            status_badge(app["status"])
        profile_summary(app)
        st.caption("Declared by the business owner at application. Only the sector, location, years trading, "
                   "staff and description are shown to the AI - never the owner's name, email or PACRA number.")
        with st.container(horizontal=True):
            for e in app["evidence"]:
                kind = evidence_class(e["source"])
                if e["fp"] in case["processed"]:
                    st.badge(f"{kind} · {e['label']} · analysed", icon=":material/check:",
                             color="green" if kind == "Primary" else "gray")
                else:
                    st.badge(f"{kind} · {e['label']} · new", icon=":material/fiber_new:", color="blue")
        if not any(evidence_class(e["source"]) == "Primary" for e in app["evidence"]):
            st.warning("Only supporting evidence (receipts, chats, pasted text) - it can be edited or faked, so "
                       "Absa policy blocks the loan until an MTN MoMo or Airtel Money wallet is connected as "
                       "primary evidence.", icon=":material/gpp_maybe:")
        if new_items and case["history"]:
            st.info(f"The business sent {len(new_items)} new document(s) since the last assessment.",
                    icon=":material/notifications:")
        signals = fraud_signals(app, applications)
        if signals:
            st.error("**Fraud checks across all applications - review before deciding**  \n"
                     + "  \n".join(f"- {html.escape(s)}" for s in signals)
                     + "  \nThese are warnings, not automatic declines.", icon=":material/policy:")

    # --- RUN ---
    with st.container(border=True, key="cg_card_run"):
        st.markdown("**:material/smart_toy: AI agent persona**")
        persona = st.radio("Agent persona", PERSONAS, captions=PERSONA_CAPTIONS, horizontal=True,
                           label_visibility="collapsed", key="persona")
        new_images = sum("image" in e for e in new_items)
        if new_items:
            label = "Run AI credit assessment" if not case["history"] else f"Analyse {len(new_items)} new document(s) & re-assess"
        else:
            label = f"Re-assess case as {persona}"
        run_clicked = st.button(label, type="primary", icon=":material/auto_awesome:", width="stretch",
                                disabled=new_images > MAX_IMAGES_PER_RUN)

    if run_clicked:
        st.session_state.pop("raw_response", None)
        with st.status(f"{persona} agent is working...", expanded=True) as status:
            try:
                app["report"] = run_analysis(
                    persona, case, app, new_items, log=lambda m: status.write(f":material/check: {m}")
                )
                if app["status"] in ("Submitted", "More info requested"):
                    app["status"] = "Under review"
                app["updated_at"] = now_iso()
                save_applications()
                status.update(label="Assessment complete", state="complete", expanded=False)
                st.session_state.toast = f"Assessment {app['report']['assessment_number']} complete"
                st.rerun()  # redraw so the new evidence shows as "Analysed"
            except ValidationError as e:
                status.update(label="AI output rejected", state="error")
                st.error("The AI returned data that failed schema validation, so it was rejected.")
                st.code(str(e))
                st.write("**Raw AI Response (for debugging):**")
                st.code(st.session_state.get("raw_response", "No response received from the AI."))
            except AIBusyError:
                status.update(label="AI service busy", state="error")
                st.error("The AI service is busy or unreachable right now (we tried 3 times). Nothing was "
                         "changed - wait a minute and run the assessment again.", icon=":material/cloud_off:")
            except genai_errors.ClientError as e:
                status.update(label="Assessment failed", state="error")
                if e.code in (401, 403):
                    st.error("Gemini rejected the API key. Check GEMINI_API_KEY in .env - copy a fresh key "
                             "from Google AI Studio (aistudio.google.com/apikey), save the file and run "
                             "the assessment again.")
                else:
                    st.error(f"Gemini returned an error: {e}")
            except Exception as e:
                status.update(label="Assessment failed", state="error")
                st.error(f"An error occurred: {e}")

    report = app["report"]
    if not report:
        return
    res = report["result"]

    # --- AI ASSESSMENT ---
    with st.container(border=True, key="cg_card_assessment"):
        title_col, badge_col = st.columns([3, 2], vertical_alignment="center")
        title_col.markdown("### :material/auto_awesome: AI credit assessment")
        title_col.caption(f"{report['persona']} · {pretty_time(report['generated_at'])} · "
                          f"Assessment {report['assessment_number']} of case {report['case_id']} · "
                          f"Audit ID `{report['audit_id']}`")
        label, icon, color = ai_recommendation(res)
        with badge_col.container(horizontal=True, horizontal_alignment="right"):
            st.badge(f"AI recommends: {label}", icon=icon, color=color)
            st.badge(credit_band(res["credit_score"]), color="gray")

        kpi_tiles(report)
        if res["decline_reasons"]:
            st.error("**Absa policy blocks this loan, whatever the AI score**  \n"
                     + "  \n".join(f"- **{r['rule']}:** {r['reason']}" for r in res["decline_reasons"]),
                     icon=":material/gpp_bad:")
        if res["suspicious_documents"]:
            st.warning("**Possible tampering - check these documents before deciding**  \n"
                       + "  \n".join(f"- **{html.escape(f['document'])}:** {f['reason']}"
                                     for f in res["suspicious_documents"])
                       + "  \nTheir records are set aside - not counted as income or expenses - so they can't "
                         "help or hurt the score. The AI can be wrong (for example with a blurry photo), so ask "
                         "the customer for a clearer copy or the original.", icon=":material/gpp_maybe:")

        overview_tab, tx_tab, history_tab, audit_tab = st.tabs(
            ["Overview", "Transactions", "Case history", "Audit trail"]
        )

        with overview_tab:
            chart_col, summary_col = st.columns([3, 2], gap="large")
            with chart_col:
                st.markdown("**Income vs. Expense (ZMW)**")
                chart_df = pd.DataFrame({
                    "Category": ["Income", "Expense", "Outstanding Debt"],
                    "Amount (ZMW)": [res["total_income"], res["total_expense"], res["outstanding_debt"]],
                })
                base = alt.Chart(chart_df).encode(
                    x=alt.X("Category:N", sort=None, title=None, axis=alt.Axis(labelAngle=0)),
                    y=alt.Y("Amount (ZMW):Q", title=None, axis=alt.Axis(format=",.0f", gridOpacity=0.4)),
                )
                bars = base.mark_bar(size=56, cornerRadiusTopLeft=4, cornerRadiusTopRight=4).encode(
                    color=alt.Color(
                        "Category:N",
                        scale=alt.Scale(domain=list(chart_df["Category"]),
                                        range=[INCOME_COLOR, EXPENSE_COLOR, DEBT_COLOR]),
                        legend=None,
                    ),
                    tooltip=["Category:N", alt.Tooltip("Amount (ZMW):Q", format=",.2f")],
                )
                labels = base.mark_text(dy=-8, fontWeight="bold").encode(
                    text=alt.Text("Amount (ZMW):Q", format=",.0f")
                )
                st.altair_chart((bars + labels).properties(height=300), width="stretch")
                score_breakdown(res)

            with summary_col:
                ai_insight(report["agent_summary"],
                           f"{html.escape(report['persona'])} · {html.escape(report['model'])} · "
                           "AI-generated - checked by Absa policy rules")
                repayment_summary(res)
                if report["policy_guardrails_applied"]:
                    st.markdown("**Policy guardrails applied**")
                    for note in report["policy_guardrails_applied"]:
                        st.warning(note, icon=":material/gavel:")
                elif res["decline_reasons"]:
                    st.info("The AI also recommended no loan. The policy reasons are listed above.",
                            icon=":material/gavel:")
                else:
                    st.success("AI recommendation is within Absa credit policy.", icon=":material/gavel:")

        with tx_tab:
            st.caption("Synthetic financial statement for the whole case. Duplicates across sources are merged. "
                       "**Primary** evidence (MTN / Airtel wallet records) decides the loan; **supporting** "
                       "evidence (receipts, chats, pasted text) adds context and is cross-checked against it.")
            st.dataframe(
                pd.DataFrame([{**t, "evidence_class": evidence_class(t["source"])} for t in report["transactions"]]),
                hide_index=True,
                width="stretch",
                column_order=("date", "description", "type", "amount_zmw", "evidence_class", "source", "evidence",
                              "document", "added_by"),
                column_config={
                    "date": "Date",
                    "description": st.column_config.TextColumn("Description", width="large"),
                    "type": "Type",
                    "amount_zmw": st.column_config.NumberColumn("Amount (ZMW)", format="%,.2f"),
                    "evidence_class": "Evidence class",
                    "source": "Source",
                    "document": "Document",
                    "evidence": "Cross-check",
                    "added_by": "Added in",
                },
            )

        with history_tab:
            st.caption("Every assessment on this case. Each run adds new evidence and re-scores the whole case.")
            history = [*report["previous_assessments"], {
                "audit_id": report["audit_id"], "generated_at": report["generated_at"],
                "persona": report["persona"], "credit_score": res["credit_score"],
                "recommended_loan_zmw": res["recommended_loan_zmw"],
                "new_evidence": report["evidence"]["new_this_run"],
            }]
            st.dataframe(
                pd.DataFrame([{
                    "#": i, "Audit ID": h["audit_id"], "Time": pretty_time(h["generated_at"]),
                    "Persona": h["persona"], "Score": h["credit_score"], "Loan (ZMW)": h["recommended_loan_zmw"],
                    "New evidence": ", ".join(h["new_evidence"]) or "None (re-assessment)",
                } for i, h in enumerate(history, 1)]),
                hide_index=True,
                width="stretch",
                column_config={"Loan (ZMW)": st.column_config.NumberColumn(format="%,.0f")},
            )

        with audit_tab:
            trace_col, export_col = st.columns([2, 1], gap="large")
            with trace_col:
                st.markdown("**Compliance & verification checks**")
                primary = report["consent"]["primary"]
                linked = report["consent"]["linked_accounts"]
                checks = [
                    (primary is not None, "Customer consent",
                     f"Confirmed by SMS code on {pretty_time(primary['verified_at'])}" if primary else "Not on record"),
                    (bool(linked), "Linked accounts proven",
                     f"{len(linked)} account(s), each on a number confirmed by SMS code" if linked
                     else "No mobile-money or WhatsApp accounts linked"),
                    (True, "Personal messages excluded",
                     f"{report['privacy']['personal_messages_removed']} non-financial chat message(s) "
                     "removed before sharing"),
                    (True, "Phone & ID numbers masked",
                     f"{report['privacy']['local_redactions']} identifier(s) removed before the AI call"),
                    (report["privacy"]["names_masked"], "Customer names masked",
                     "Names replaced with Customer A, B, C" if report["privacy"]["names_masked"]
                     else "Not applied - use Synthetic Data Engineer to mask names"),
                    (res["verified_income_pct"] > 0, "Primary evidence",
                     f"{res['verified_income_pct']}% of income backed by MTN / Airtel wallet records"
                     if res["verified_income_pct"] else "No wallet records - only supporting evidence, "
                     "which cannot approve a loan on its own"),
                    (not any(r["rule"] in ("Verified wallet", "Dated records", "6 months of history", "Regular sales", "Verified cash flow")
                             for r in res["decline_reasons"]), "Verified history",
                     f"{res['trading_history']['span_days']} days of wallet records, sales in "
                     f"{res['trading_history']['active_months']} month(s) (needs {MIN_HISTORY_DAYS} days, "
                     f"{MIN_ACTIVE_MONTHS} months)"),
                    (True, "Supporting evidence cross-checked",
                     f"{res['self_reported_confirmed']} receipt / chat record(s) confirm a wallet payment; "
                     f"{res['self_reported_unchecked']} can't be checked and don't count towards the loan size"),
                    (not res["suspicious_documents"], "Document checks",
                     f"{len(res['suspicious_documents'])} document(s) flagged for possible tampering"
                     if res["suspicious_documents"] else "No signs of tampering found"),
                    (not signals, "Cross-application fraud checks",
                     f"{len(signals)} warning(s) - see the application card" if signals
                     else "No shared documents, shared phone numbers or last-minute income bursts"),
                    (bool(res.get("score_factors")), "Explainable score",
                     "Absa scorecard - same records, same score" if res.get("score_factors")
                     else "Scored before the scorecard existed - re-assess"),
                    (True, "Policy guardrails", f"{len(report['policy_guardrails_applied'])} applied by the rules engine"),
                    (report["analyst_decision"] is not None and app["status"] != "Awaiting second approval",
                     "Human decision",
                     "Waiting for a second analyst to confirm" if app["status"] == "Awaiting second approval"
                     else f"Recorded by {report['analyst_decision']['analyst']}" if report["analyst_decision"]
                     else "Awaiting your decision"),
                ]
                passed_count = sum(passed for passed, _, _ in checks)
                st.caption(f"{passed_count} of {len(checks)} checks passed")
                for passed, title, detail in checks:
                    mark = ":green[:material/check_circle:]" if passed else ":orange[:material/error:]"
                    st.markdown(f"{mark} **{title}** · {detail}")
                with st.expander(f"AI agent steps ({len(report['agent_trace'])})", icon=":material/smart_toy:"):
                    for i, line in enumerate(report["agent_trace"], 1):
                        st.markdown(f"{i}. {line}")
            with export_col:
                # The analyst reads the checklist; the JSON file is the machine-readable compliance record
                decision = report["analyst_decision"]
                st.markdown("**Audit record**")
                st.markdown(
                    f":gray[Audit ID:] `{report['audit_id']}`  \n"
                    f":gray[Case:] {report['case_id']} · assessment {report['assessment_number']}  \n"
                    f":gray[Generated:] {pretty_time(report['generated_at'])}  \n"
                    f":gray[AI agent:] {report['persona']} · {report['model']}  \n"
                    f":gray[Decision:] "
                    + (f"{decision['outcome']} · {pretty_time(decision['decided_at'])}" if decision else "Pending")
                )
                st.download_button(
                    "Download audit report (JSON)",
                    data=json.dumps(report, indent=2),
                    file_name=f"creditgenie_{report['audit_id']}.json",
                    mime="application/json",
                    type="primary",
                    icon=":material/download:",
                    width="stretch",
                    on_click="ignore",
                )
                st.caption("The full structured report for auditors and Absa's records. Raw messages and images "
                           "are deleted once analysed - only SHA-256 fingerprints are kept.")
                with st.expander(f"Decision log ({len(app['decisions'])})", icon=":material/history:"):
                    if not app["decisions"]:
                        st.caption("No decisions yet.")
                    for d in app["decisions"]:
                        if d["event"] == "Decision":
                            st.markdown(f"**{d['outcome']}**"
                                        + (f" · ZMW {d['amount_zmw']:,.0f}" if d["amount_zmw"] else "")
                                        + f" · {html.escape(d['analyst'])}  \n:gray[{pretty_time(d['decided_at'])} · "
                                        f"assessment {d['based_on_audit_id']}"
                                        + (" · override" if d["override_of_recommendation"] else "") + "]")
                        else:
                            st.markdown(f"**Second approval: {d['outcome']}** · {html.escape(d['by'])}  \n"
                                        f":gray[{pretty_time(d['at'])}]")
                    st.caption("Every decision is kept here, even after a re-assessment. Entries are never edited.")

    # --- ANALYST DECISION (human in the loop) ---
    with st.container(border=True, key="cg_card_decision"):
        st.markdown("### :material/gavel: Your decision")
        decision = report["analyst_decision"]
        if decision and app["status"] == "Awaiting second approval":
            offer = decision["offer"]
            st.warning(f"**Awaiting second approval** · {html.escape(decision['analyst'])} approved ZMW "
                       f"{offer['amount_zmw']:,.0f} over {offer['term_months']} months · "
                       f"{pretty_time(decision['decided_at'])}  \n"
                       + ("This overrides the scorecard and policy recommendation. "
                          if decision["override_of_recommendation"] else "")
                       + (f"Loans above ZMW {FOUR_EYES_LIMIT_ZMW:,} " if offer["amount_zmw"] > FOUR_EYES_LIMIT_ZMW
                          else "Overrides ")
                       + "need a second analyst to confirm (four-eyes rule)."
                       + (f"  \nNote: {html.escape(decision['note'])}" if decision["note"] else ""),
                       icon=":material/group:")
            suffix = decision["based_on_audit_id"]
            name_col, confirm_col, reject_col = st.columns([3, 1, 1], vertical_alignment="bottom")
            name_col.text_input("Second approver's name", key=f"second_approver_{suffix}",
                                placeholder="Must be a different analyst")
            confirm_col.button("Confirm", type="primary", icon=":material/check:", width="stretch",
                               on_click=second_approval, args=(app["ref"], True))
            reject_col.button("Reject", icon=":material/undo:", width="stretch",
                              on_click=second_approval, args=(app["ref"], False))
            if flash_msg and flash_msg[0] == "error":
                st.error(flash_msg[1])
        elif decision:
            offer = decision.get("offer")
            st.success(f"**{app['status']}**"
                       + (f" · ZMW {offer['amount_zmw']:,.0f} over {offer['term_months']} months "
                          f"(ZMW {offer['monthly_instalment']:,.0f}/month)" if offer
                          else f" · ZMW {decision['amount_zmw']:,.0f}" if decision["amount_zmw"] else "")
                       + f" · {html.escape(decision['analyst'])} · {pretty_time(decision['decided_at'])}"
                       + (f"  \nSecond approval: {html.escape(decision['second_approval']['by'])}"
                          if decision.get("second_approval") else "")
                       + (f"  \nNote: {html.escape(decision['note'])}" if decision["note"] else ""),
                       icon=":material/how_to_reg:")
            if decision["override_of_recommendation"]:
                st.caption("Flagged in the audit report as an override of the scorecard / policy recommendation.")
            response = app["offer_response"]
            if app["status"] == "Approved":
                if response:
                    st.markdown(f":{'green' if response['response'] == 'Accepted' else 'red'}[:material/handshake:] "
                                f"**Customer {response['response'].lower()} the offer** · {pretty_time(response['at'])}")
                else:
                    st.caption(":material/hourglass_top: Waiting for the customer to accept or decline the offer.")
            st.caption("Run a new assessment to make another decision.")
        else:
            st.caption("The AI recommends; you decide. Your decision is sent to the SME Portal and stored in the audit report.")
            if res["decline_reasons"]:
                st.caption(":material/info: If you decline or ask for more information, the customer sees the "
                           "policy reasons above and what they can do about each one.")
            if res["recommended_loan_zmw"] <= 0:
                default_outcome = "Decline"
            elif res["suspicious_documents"]:
                default_outcome = "Request more info"
            else:
                default_outcome = "Approve"
            suffix = report["audit_id"]
            outcome = st.segmented_control("Outcome", ("Approve", "Request more info", "Decline"),
                                           default=default_outcome, required=True,
                                           key=f"decision_outcome_{suffix}")
            if outcome == "Approve":
                term = report["application"].get("term_months", 6)
                amount_col, term_col = st.columns(2)
                amount = amount_col.number_input("Loan amount to offer (ZMW)", min_value=0.0,
                                                 value=float(res["recommended_loan_zmw"]), step=500.0,
                                                 key=f"decision_amount_{suffix}",
                                                 help="Pre-filled with the scorecard and affordability recommendation.")
                term = term_col.selectbox("Repay over", LOAN_TERMS, index=LOAN_TERMS.index(term),
                                          key=f"decision_term_{suffix}", format_func=lambda m: f"{m} months")
                plan = repayment_plan(amount, term)
                st.caption(f":material/calculate: ZMW {plan['monthly_instalment']:,.0f}/month · "
                           f"ZMW {plan['total_repayable']:,.0f} in total"
                           + (f" · above ZMW {FOUR_EYES_LIMIT_ZMW:,}, so a second analyst must confirm"
                              if amount > FOUR_EYES_LIMIT_ZMW else "")
                           + (" · above the recommendation, so a second analyst must confirm"
                              if amount > res["recommended_loan_zmw"] else ""))
            placeholder = {"Approve": "Optional - e.g. first repayment due 30 days after payout",
                           "Request more info": "e.g. Please upload last month's supplier receipts",
                           "Decline": "Explain the reason - the customer will see this"}[outcome]
            st.text_area("Note to the customer", key=f"decision_note_{suffix}", placeholder=placeholder, height=80)
            st.button("Record decision", type="primary", icon=":material/gavel:",
                      on_click=record_decision, args=(app["ref"],))
            if flash_msg and flash_msg[0] == "error":
                st.error(flash_msg[1])
