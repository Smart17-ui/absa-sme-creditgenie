"""VIEW 1: SME PORTAL - the business owner verifies, shares data and applies."""
import html
import uuid

import streamlit as st

from creditgenie.applications import (OWNER_ROLES, PROFILE_FIELDS, PROVINCES, REQUIRED_PROFILE, SECTORS, VIEWERS,
                                      empty_case, open_application, save_applications, sme_application,
                                      start_new_application)
from creditgenie.config import DECIDED, LOAN_PURPOSES, MAX_IMAGES_PER_RUN
from creditgenie.feeds import MAX_NUMBERS_PER_PROVIDER, SIMULATED_FEEDS, VERIFIED_SOURCES, keep_money_messages
from creditgenie.policy import LOAN_TERMS, MIN_LOAN_ZMW, repayment_plan
from creditgenie.ui.components import application_rows, hero, profile_summary, status_badge, step_header
from creditgenie.utils import fingerprint, now_iso, pretty_time
from creditgenie.verification import (CONSENT_TEXT, DEMO_BUSINESSES, accounts_for, disconnect, link_account_dialog,
                                      load_demo, load_sample, send_otp, unlink_account, verify_otp)


def application_tracker(app):
    stages = ["Submitted", "Under review", "Decision"]
    current = {"Submitted": 0, "Under review": 1, "More info requested": 1,
               "Awaiting second approval": 1}.get(app["status"], 3)
    st.html('<div class="cg-track">' + "".join(
        f'<div class="cg-track-step {"done" if i < current else "now" if i == current else ""}">{s}</div>'
        for i, s in enumerate(stages)) + "</div>")


@st.dialog("How we protect your data", width="medium")
def privacy_dialog():
    connection = st.session_state.connection
    if connection:
        st.success(f"You gave consent on {pretty_time(connection['verified_at'])}.", icon=":material/verified_user:")
    else:
        st.info("You haven't shared anything yet. Nothing is shared until you verify your number and agree.",
                icon=":material/lock:")
    st.markdown(
        ":material/how_to_reg: **You decide what is shared**  \n"
        "We only read the accounts you connect and the files you upload. "
        "You can withdraw consent at any time with **Revoke consent**.\n\n"
        ":material/chat: **Only messages about money**  \n"
        "From WhatsApp we only take messages about sales, payments and debts. Personal messages are removed "
        "in this portal and never reach Absa. Use **Preview what will be shared** to check.\n\n"
        ":material/phonelink_lock: **Every number is proven**  \n"
        "Each wallet or WhatsApp number you link is confirmed with an SMS code, so nobody can link "
        "someone else's account.\n\n"
        ":material/visibility_off: **Your customers' details stay private**  \n"
        "Phone numbers and ID numbers in your records are removed *before* our AI reads them.\n\n"
        ":material/event_available: **Used only for this application**  \n"
        "Your records are used to assess this loan. Your receipts and messages are deleted once our AI has "
        "read them - Absa keeps only the figures it found. You can move your applications to trash or "
        "delete them from your list at any time.\n\n"
        ":material/person: **A person makes the decision**  \n"
        "Our AI prepares a report, but an Absa credit analyst reviews every application and makes the final decision."
    )
    st.caption(CONSENT_TEXT)


def respond_to_offer(ref, response):
    app = st.session_state.applications[ref]
    app["offer_response"] = {"response": response, "at": now_iso()}
    app["updated_at"] = app["offer_response"]["at"]
    save_applications()
    st.session_state.toast = ("Offer accepted - an Absa relationship manager will contact you to sign."
                              if response == "Accepted" else "You declined the offer.")


def loan_offer(app):
    """The approved loan as the customer sees it: amount, repayments, and Accept / Decline."""
    offer = app["decision"].get("offer")
    if not offer:  # approved before repayment plans existed
        st.success(f"Good news! Absa can offer you **ZMW {app['decision']['amount_zmw']:,.0f}**. "
                   "A relationship manager will contact you to finalise the loan.", icon=":material/celebration:")
        return
    st.success(f"Good news! Absa can offer you **ZMW {offer['amount_zmw']:,.0f}**.", icon=":material/celebration:")
    st.html('<div class="cg-kpis">' + "".join(
        f'<div class="cg-kpi"><div class="cg-kpi-label">{label}</div><div class="cg-kpi-value">{value}</div>'
        f'<div class="cg-kpi-note">{note}</div></div>' for label, value, note in [
            ("Loan amount", f'<span class="cg-cur">ZMW</span>{offer["amount_zmw"]:,.0f}', app["purpose"]),
            ("Monthly repayment", f'<span class="cg-cur">ZMW</span>{offer["monthly_instalment"]:,.0f}',
             f"for {offer['term_months']} months"),
            ("Total to repay", f'<span class="cg-cur">ZMW</span>{offer["total_repayable"]:,.0f}',
             f"Interest {offer['annual_rate']:.0%} a year (illustrative)"),
        ]) + "</div>")
    response = app["offer_response"]
    if response:
        if response["response"] == "Accepted":
            st.info(f"You accepted this offer on {pretty_time(response['at'])}. An Absa relationship manager will "
                    "contact you to sign the loan agreement.", icon=":material/handshake:")
        else:
            st.caption(f"You declined this offer on {pretty_time(response['at'])}.")
    else:
        with st.container(horizontal=True):
            st.button("Accept offer", type="primary", icon=":material/handshake:",
                      on_click=respond_to_offer, args=(app["ref"], "Accepted"))
            st.button("Decline offer", icon=":material/close:", on_click=respond_to_offer,
                      args=(app["ref"], "Declined"))


def sme_portal(flash_msg):
    applications = st.session_state.applications
    application = sme_application()
    # Defaults set here rather than on the widgets, so the demo businesses can fill these fields in
    for key, default in {"requested_zmw": 10_000, "term_months": 6, "profile_years_trading": 1,
                         "profile_employees": 0}.items():
        st.session_state.setdefault(key, default)
    hero("Business owner · secure session", "Working capital, <em>powered by your mobile money</em>",
         "Apply for a business loan by securely sharing your MTN MoMo, Airtel Money, WhatsApp and receipts. "
         "No paperwork, no branch visit.",
         ["Bank-grade encryption", "You control consent", "Reviewed by an Absa analyst"],
         stats=[("Paperwork", "None"), ("Loan size", "Up to K500k"), ("Data law", "DPA 2021")])

    with st.container(border=True, key="cg_card_trust", horizontal=True, vertical_alignment="center"):
        st.markdown(":material/lock: **Your data is protected.** You choose what to share, your customers' "
                    "details are hidden from our AI, and an Absa analyst makes the final decision.")
        if st.button("How we protect your data", icon=":material/info:", type="tertiary"):
            privacy_dialog()

    mine = [a for a in reversed(applications.values()) if not a["deleted"]["sme"]]  # newest first
    if mine:
        active = [a for a in mine if not a["trashed"]["sme"]]
        in_progress = [a for a in active if a["status"] not in DECIDED]
        approved = [a for a in active if a["status"] == "Approved"]
        declined = [a for a in active if a["status"] == "Declined"]
        trashed = [a for a in mine if a["trashed"]["sme"]]
        with st.container(border=True, key="cg_card_my_apps"):
            title_col, new_col = st.columns([3, 1], vertical_alignment="center")
            title_col.markdown("**:material/folder_open: Your applications**")
            new_col.button("New application", on_click=start_new_application, icon=":material/add:",
                           width="stretch", disabled=application is None and not st.session_state.connection)
            progress_tab, approved_tab, declined_tab, trash_tab = st.tabs([
                f":material/hourglass_top: In progress ({len(in_progress)})",
                f":material/check_circle: Approved ({len(approved)})",
                f":material/cancel: Declined ({len(declined)})",
                f":material/delete: Trash ({len(trashed)})",
            ])
            with progress_tab:
                st.caption("Waiting for Absa, or waiting for more documents from you.")
                application_rows(in_progress, "sme", st.session_state.current_ref, open_application,
                                 "Nothing in progress - start a new application above.")
            with approved_tab:
                st.caption("Open one to see your loan offer and accept or decline it.")
                application_rows(approved, "sme", st.session_state.current_ref, open_application,
                                 "No approved applications yet.")
            with declined_tab:
                st.caption("Open one to see why, and what you can do before applying again.")
                application_rows(declined, "sme", st.session_state.current_ref, open_application,
                                 "No declined applications.")
            with trash_tab:
                st.caption("Trash only hides an application from your list - Absa still has it. "
                           "Restore it, or delete it from your list for good.")
                application_rows(trashed, "sme", None, None, "Trash is empty.", trash=True)

    app = application
    if app:
        with st.container(border=True, key="cg_card_status"):
            head_col, badge_col = st.columns([3, 1], vertical_alignment="center")
            head_col.markdown(f"**Application {app['ref']}** · ZMW {app['requested_zmw']:,.0f} for "
                              f"{app['purpose'].lower()}")
            head_col.caption(f"Submitted {pretty_time(app['submitted_at'])} · last update {pretty_time(app['updated_at'])}")
            with badge_col.container(horizontal=True, horizontal_alignment="right"):
                status_badge(app["status"])
            application_tracker(app)

            decision = app["decision"]
            if app["status"] == "Approved":
                loan_offer(app)
            elif app["status"] == "Declined":
                st.error("We are unable to offer a loan at this time.", icon=":material/info:")
            elif app["status"] == "More info requested":
                st.warning("**Absa needs more information.** Add the documents below and send them to Absa."
                           + (f"  \n{decision['note']}" if decision["note"] else ""), icon=":material/help:")
            reasons = (decision or {}).get("reasons", [])
            if reasons:
                why_col, fix_col = st.columns(2, gap="large")
                why_col.markdown("**Why**  \n" + "  \n".join(f"- {r['reason']}" for r in reasons))
                fix_col.markdown("**What you can do**  \n" + "  \n".join(f"- {r['fix']}" for r in reasons))
            elif app["status"] != "Approved":
                st.info("Thank you - your application is with an Absa credit analyst. "
                        "You can still send extra documents below.", icon=":material/hourglass_top:")
            if decision and decision.get("note") and app["status"] in ("Approved", "Declined"):
                st.caption(f"Message from Absa: {decision['note']}")

        if app["status"] in ("Approved", "Declined"):
            st.caption("To apply again, use **New application** above.")
            return

    # --- STEP 1: VERIFY ---
    connection = st.session_state.connection
    with st.container(border=True, key="cg_card_verify"):
        step_header(1, "Verify your business", "We send a one-time code to your business mobile number.")
        if connection:
            with st.container(horizontal=True, vertical_alignment="center"):
                st.badge("Verified", icon=":material/verified_user:", color="green")
                st.markdown(f"**{html.escape(connection['business_name'])}** · {connection['phone_masked']} · "
                            f"{connection['network']}")
                st.button("Revoke consent", on_click=disconnect, icon=":material/link_off:", type="tertiary")
        else:
            name_col, phone_col = st.columns(2)
            name_col.text_input("Registered business name", key="business_name",
                                placeholder="e.g. Chilenje Grocery Supplies")
            phone_col.text_input("Business mobile number", key="phone_input", placeholder="e.g. 097 123 4567")
            st.checkbox("I agree to share my MTN MoMo, Airtel Money and WhatsApp Business transactions "
                        "with Absa for this application.", key="consent_given", help=CONSENT_TEXT)
            with st.container(horizontal=True, vertical_alignment="bottom"):
                st.button("Send verification code", on_click=send_otp, icon=":material/sms:")
                if "otp" in st.session_state:
                    st.text_input("6-digit code", key="otp_input", max_chars=6, width=160)
                    st.button("Verify", on_click=verify_otp, type="primary")
            if not app:
                with st.container(horizontal=True, vertical_alignment="center"):
                    st.caption(":material/science: **Demo:** fill everything in with one click -")
                    for kind, demo in DEMO_BUSINESSES.items():
                        st.button(demo["button"], key=f"demo_{kind}", on_click=load_demo, args=(kind,),
                                  type="tertiary", icon=demo["icon"], help=demo["help"])
        if flash_msg:
            kind, message = flash_msg
            if kind == "sms":
                st.html(f'<div class="cg-sms">{html.escape(message)}</div>')
            else:
                getattr(st, kind)(message)

    # --- STEP 2: ABOUT THE BUSINESS ---
    with st.container(border=True, key="cg_card_profile"):
        step_header(2, "About you and your business",
                    "Helps the Absa analyst understand your business. Fields marked * are required.")
        if app:
            profile_summary(app)
        else:
            owner_col, role_col, email_col = st.columns([2, 1, 2])
            owner_col.text_input("Owner's full name *", key="profile_owner_name", placeholder="e.g. Mutale Banda")
            role_col.selectbox("Position", OWNER_ROLES, key="profile_owner_role")
            email_col.text_input("Email (optional)", key="profile_owner_email", placeholder="e.g. mutale@example.com")
            sector_col, town_col, province_col = st.columns(3)
            sector_col.selectbox("Sector", SECTORS, key="profile_sector")
            town_col.text_input("Town / district *", key="profile_town", placeholder="e.g. Chilenje, Lusaka")
            province_col.selectbox("Province", PROVINCES, key="profile_province")
            years_col, staff_col, pacra_col = st.columns(3)
            years_col.number_input("Years trading", min_value=0, max_value=60, key="profile_years_trading")
            staff_col.number_input("Employees (besides you)", min_value=0, max_value=500, key="profile_employees")
            pacra_col.text_input("PACRA registration no. (optional)", key="profile_pacra_number",
                                 help="Leave blank if the business is not registered yet - you can still apply.")
            st.text_area("What does the business do? *", key="profile_description", height=80,
                         placeholder="e.g. Grocery shop selling mealie meal, cooking oil and household goods "
                                     "to the Chilenje community. Open 7 days a week.")
    profile = {field: st.session_state.get(f"profile_{field}") for field in PROFILE_FIELDS}
    profile_missing = [PROFILE_FIELDS[f][0] for f in REQUIRED_PROFILE if not str(profile[f] or "").strip()]

    # --- STEP 3: SHARE DATA ---
    with st.container(border=True, key="cg_card_share"):
        step_header(3, "Share your business records",
                    "Connect your accounts, upload receipts, or paste messages. The more you share, the better.")
        tab_sync, tab_image, tab_text = st.tabs(["Connect accounts", "Upload receipts", "Paste messages"])

        with tab_sync:
            st.caption("Your wallets and WhatsApp can use different numbers - link each one. "
                       "Prototype: the MTN, Airtel and WhatsApp connections use sample data.")
            for source, icon in zip(SIMULATED_FEEDS, (":material/phone_android:",
                                                       ":material/account_balance_wallet:",
                                                       ":material/chat:")):
                accounts = accounts_for(source)
                label = "WhatsApp Business" if source == "WhatsApp" else source
                name_col, status_col, btn_col = st.columns([3, 3, 2], vertical_alignment="center")
                name_col.markdown(f"{icon} **{label}**")
                if accounts:
                    status_col.badge(f"{len(accounts)} of {MAX_NUMBERS_PER_PROVIDER} numbers linked",
                                     icon=":material/check:", color="green")
                elif connection:
                    status_col.badge("Ready to link", color="gray")
                else:
                    status_col.badge("Verify first", icon=":material/lock:", color="gray")
                full = len(accounts) >= MAX_NUMBERS_PER_PROVIDER
                if btn_col.button("Add number" if accounts else "Connect", key=f"sync_{source}",
                                  disabled=connection is None or full, icon=":material/add_link:", width="stretch",
                                  help=f"Up to {MAX_NUMBERS_PER_PROVIDER} numbers" if full else None):
                    link_account_dialog(source)
                for account_id, account in accounts.items():
                    num_col, info_col, unlink_col = st.columns([3, 3, 2], vertical_alignment="center")
                    num_col.markdown(f":gray[:material/subdirectory_arrow_right:] {account['phone_masked']} · "
                                     f"{account['network']}")
                    info_col.caption(f"{account['verification']} · synced "
                                     f"{st.session_state.synced[account_id][1]:%H:%M}")
                    unlink_col.button("Unlink", key=f"unlink_{account_id}", on_click=unlink_account,
                                      args=(account_id,), icon=":material/link_off:", type="tertiary",
                                      width="stretch")
            if st.session_state.synced:
                with st.expander("Preview what will be shared"):
                    for account_id, (feed, _) in st.session_state.synced.items():
                        account = st.session_state.linked[account_id]
                        shown, removed = (keep_money_messages(feed) if account["provider"] == "WhatsApp"
                                          else (feed, 0))
                        st.markdown(f"**{account['provider']}** · {account['phone_masked']}"
                                    + (f" · :green[{removed} personal message(s) removed]" if removed else ""))
                        st.text(shown)

        with tab_image:
            uploaded_files = st.file_uploader(
                f"Receipts, invoices or SMS/WhatsApp screenshots (up to {MAX_IMAGES_PER_RUN} at a time)",
                type=["jpg", "jpeg", "png"],
                accept_multiple_files=True,
                key=f"uploader_{st.session_state.uploader_key}",
            )
            if uploaded_files:
                thumbs = st.columns(6)
                for i, f in enumerate(uploaded_files):
                    thumbs[i % 6].image(f, caption=f.name, width="stretch")

        with tab_text:
            st.button("Load sample messages", on_click=load_sample, icon=":material/description:")
            messy_data = st.text_area("Paste WhatsApp or SMS messages about your sales and payments",
                                      key="messy_data", height=160,
                                      placeholder="e.g. Received 4,000 ZMW from Mama Chileshe for mealie meal...")

    # Everything on screen that hasn't been sent to Absa yet. Chats are cut down to money messages
    # here, in the portal, so personal messages never reach the bank or the AI.
    evidence = []
    for account_id, (feed, _) in st.session_state.synced.items():
        account = st.session_state.linked[account_id]
        source = account["provider"]
        text, removed = keep_money_messages(feed) if source == "WhatsApp" else (feed, 0)
        evidence.append({"fp": fingerprint(text.encode()), "label": f"{source} {account['phone_masked']}",
                         "source": source, "text": text, "filtered_out": removed})
    if messy_data.strip():
        text, removed = keep_money_messages(messy_data)
        if text:
            evidence.append({"fp": fingerprint(text.encode()), "label": "Pasted messages",
                             "source": "Pasted text", "text": text, "filtered_out": removed})
        else:
            with tab_text:
                st.warning("None of these messages mention money, so there is nothing to share. "
                           "Paste messages about sales, payments or debts.")
    for f in uploaded_files:
        data = f.getvalue()
        evidence.append({"fp": fingerprint(data), "label": f"Receipt: {f.name}", "source": "Image", "image": data})
    sent = {e["fp"] for e in app["evidence"]} if app else set()
    unsent = [e for e in evidence if e["fp"] not in sent]
    unsent_images = sum("image" in e for e in unsent)

    # --- STEP 4: APPLY ---
    with st.container(border=True, key="cg_card_apply"):
        if app:
            step_header(4, "Send more documents", "Anything new is added to your existing application.")
        else:
            step_header(4, "Apply", "Tell us how much you need. An Absa credit analyst reviews every application.")
            amount_col, term_col, purpose_col = st.columns(3)
            amount_col.number_input("Amount needed (ZMW)", min_value=MIN_LOAN_ZMW, max_value=500_000, step=500,
                                    key="requested_zmw")
            term_col.selectbox("Repay over", LOAN_TERMS, key="term_months", format_func=lambda m: f"{m} months")
            purpose_col.selectbox("What is the loan for?", LOAN_PURPOSES, key="purpose")
            estimate = repayment_plan(float(st.session_state.requested_zmw), st.session_state.term_months)
            st.caption(f":material/calculate: About **ZMW {estimate['monthly_instalment']:,.0f} a month** for "
                       f"{estimate['term_months']} months (ZMW {estimate['total_repayable']:,.0f} in total, at an "
                       f"illustrative {estimate['annual_rate']:.0%} a year). Absa may offer less if your verified "
                       "cash flow can't comfortably cover the repayments.")

        with st.container(horizontal=True):
            for e in unsent:
                st.badge(f"Ready · {e['label']}", icon=":material/upload:", color="blue")
            for e in (app["evidence"] if app else []):
                st.badge(f"Sent · {e['label']}", icon=":material/check:", color="gray")
        if unsent_images > MAX_IMAGES_PER_RUN:
            st.warning(f"Please send at most {MAX_IMAGES_PER_RUN} images at a time.")
        if connection is None:
            st.caption("Verify your business number in step 1 to continue.")
        elif not app and profile_missing:
            st.caption(f"Complete step 2 to continue: {', '.join(profile_missing)}.")
        elif not any(a["provider"] in VERIFIED_SOURCES
                     for a in [*st.session_state.linked.values(), *(app["linked_accounts"].values() if app else [])]):
            st.info("You can apply with receipts and messages, but they only support your application. Absa "
                    "can only lend once you connect an MTN MoMo or Airtel Money wallet - that's how we confirm "
                    "your sales.", icon=":material/info:")

        clicked = st.button("Send to Absa" if app else "Submit application", type="primary",
                            icon=":material/send:", width="stretch",
                            disabled=connection is None or not unsent or unsent_images > MAX_IMAGES_PER_RUN
                            or (not app and bool(profile_missing)))
    if clicked:
        stamp = now_iso()
        if app:
            app["evidence"] += unsent
            app["linked_accounts"].update(st.session_state.linked)
            app["status"] = "Under review" if app["status"] == "More info requested" else app["status"]
            app["updated_at"] = stamp
            st.session_state.toast = f"{len(unsent)} new document(s) sent to Absa"
        else:
            ref = f"APP-{uuid.uuid4().hex[:6].upper()}"
            applications[ref] = {
                "ref": ref,
                "business_name": connection["business_name"],
                "requested_zmw": float(st.session_state.requested_zmw),
                "term_months": st.session_state.term_months,
                "purpose": st.session_state.purpose,
                "status": "Submitted",
                "submitted_at": stamp,
                "updated_at": stamp,
                "profile": {k: v.strip() if isinstance(v, str) else v for k, v in profile.items()},
                "consent": dict(connection),
                "evidence": unsent,
                "linked_accounts": dict(st.session_state.linked),
                "decision": None,  # the latest decision; the full history is in "decisions"
                "decisions": [],
                "offer_response": None,
                "case": empty_case(),
                "report": None,  # latest AI assessment
                "trashed": dict.fromkeys(VIEWERS),
                "deleted": dict.fromkeys(VIEWERS, False),
            }
            st.session_state.current_ref = ref
            st.session_state.toast = "Application submitted. Switch to Absa Analyst to review it."
        save_applications()
        st.rerun()
