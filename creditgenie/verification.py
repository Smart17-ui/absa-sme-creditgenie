"""Simulated account verification: SMS OTP, consent and linking wallet / WhatsApp numbers."""
import datetime
import hashlib
import hmac
import re
import secrets
import time
import unicodedata

import streamlit as st

from creditgenie.feeds import LINK_HELP, PROVIDER_NETWORK, SAMPLE_DATA, SIMULATED_FEEDS, simulated_feed
from creditgenie.utils import flash, now_iso


CONSENT_TEXT = (
    "The business owner consents to Absa Bank Zambia accessing their MTN MoMo, Airtel Money and "
    "WhatsApp Business transaction history for this credit assessment only, in line with the "
    "Data Protection Act No. 3 of 2021. Consent can be revoked at any time."
)
OTP_TTL_SECONDS = 300
MAX_OTP_ATTEMPTS = 3
ZAMBIAN_MOBILE_RE = re.compile(r"^(?:\+?260|0)?((?:9[5-7]|7[5-7])\d{7})$")
NETWORK_BY_PREFIX = {"96": "MTN", "76": "MTN", "97": "Airtel", "77": "Airtel", "95": "Zamtel", "75": "Zamtel"}


def normalise_phone(raw):
    # Keep only digits and "+": numbers copied from WhatsApp or a contact card often carry invisible
    # characters or special dashes. NFKC also turns full-width digits into ordinary ones.
    match = ZAMBIAN_MOBILE_RE.match(re.sub(r"[^\d+]", "", unicodedata.normalize("NFKC", raw), flags=re.ASCII))
    return f"+260{match.group(1)}" if match else None


def invalid_phone_message(raw):
    digits = re.sub(r"\D", "", unicodedata.normalize("NFKC", raw), flags=re.ASCII)
    return (f"\"{raw.strip()}\" isn't a Zambian mobile number we recognise ({len(digits)} digits read). "
            "Use a 10-digit MTN, Airtel or Zamtel number, e.g. 097 123 4567.")


def mask_phone(phone):
    return f"{phone[:4]} {phone[4:6]} *** {phone[-4:]}"


def make_connection(business_name, phone, verification):
    """The record of a verified business number and the owner's consent."""
    return {
        "business_name": business_name,
        "phone_masked": mask_phone(phone),
        "phone_sha256": hashlib.sha256(phone.encode()).hexdigest(),
        "network": NETWORK_BY_PREFIX[phone[4:6]],
        "verification": verification,
        "consent_text": CONSENT_TEXT,
        "verified_at": now_iso(),
    }


def send_otp():
    phone = normalise_phone(st.session_state.phone_input)
    if not st.session_state.business_name.strip():
        flash("error", "Enter the registered business name.")
    elif phone is None:
        flash("error", invalid_phone_message(st.session_state.phone_input))
    elif not st.session_state.consent_given:
        flash("error", "Please tick the consent box before connecting any account.")
    else:
        code = f"{secrets.randbelow(10**6):06d}"
        st.session_state.otp = {"code": code, "phone": phone, "sent_at": time.time(), "attempts": 0}
        flash("sms", f"Absa: your verification code is {code}. Valid for 5 minutes. "
                     f"Never share this code. (Simulated SMS to {mask_phone(phone)})")


def verify_otp():
    otp = st.session_state.get("otp")
    entered = st.session_state.otp_input.strip()
    if otp is None:
        flash("error", "Request a verification code first.")
    elif time.time() - otp["sent_at"] > OTP_TTL_SECONDS:
        del st.session_state.otp
        flash("error", "The code has expired. Request a new one.")
    elif hmac.compare_digest(entered, otp["code"]):
        st.session_state.connection = make_connection(st.session_state.business_name.strip(), otp["phone"],
                                                       "SMS OTP (simulated)")
        del st.session_state.otp
        st.session_state.otp_input = ""
        flash("success", "Number verified. You can now connect your accounts.")
    else:
        otp["attempts"] += 1
        left = MAX_OTP_ATTEMPTS - otp["attempts"]
        if left <= 0:
            del st.session_state.otp
            flash("error", "Too many incorrect attempts. Request a new code.")
        else:
            flash("error", f"Incorrect code. {left} attempt(s) left.")


def disconnect():
    st.session_state.connection = None
    st.session_state.linked = {}
    st.session_state.synced = {}
    st.session_state.pop("link_otp", None)
    flash("info", "Consent revoked. All linked accounts were disconnected.")


def load_sample():
    st.session_state.messy_data = SAMPLE_DATA


# An SME may run several wallets and use different numbers for MoMo, Airtel Money and WhatsApp.
# Each linked number must be proven: either it is the verified primary number, or it gets its own SMS code.
def accounts_for(provider):
    return {aid: a for aid, a in st.session_state.linked.items() if a["provider"] == provider}


def link_account(provider, phone_masked, phone_sha256, network, verification):
    account_id = f"{provider}|{phone_sha256[:12]}"
    # The first number keeps the demo story; extra numbers get their own sample history
    feed = SIMULATED_FEEDS[provider] if not accounts_for(provider) else simulated_feed(provider, phone_sha256)
    st.session_state.linked[account_id] = {
        "provider": provider,
        "phone_masked": phone_masked,
        "phone_sha256": phone_sha256,
        "network": network,
        "verification": verification,
        "linked_at": now_iso(),
    }
    time.sleep(0.8)  # simulate the network round-trip of a live API fetch
    st.session_state.synced[account_id] = (feed, datetime.datetime.now())
    st.session_state.toast = f"{provider} linked to {phone_masked}"


def unlink_account(account_id):
    st.session_state.linked.pop(account_id, None)
    st.session_state.synced.pop(account_id, None)


# Two ready-made applicants for demos: one Absa can lend to, and one it can't yet
DEMO_BUSINESSES = {
    "strong": {
        "button": "Established shop",
        "icon": ":material/storefront:",
        "help": "6 months of MTN MoMo and Airtel Money records plus WhatsApp - should qualify",
        "business_name": "Chilenje Grocery Supplies",
        "phone": "+260965550199",
        "links": [("MTN MoMo", None), ("Airtel Money", "+260977000111"), ("WhatsApp", None)],
        "messages": "",
        "profile": {"owner_name": "Mutale Banda", "owner_role": "Owner", "owner_email": "mutale@example.com",
                    "sector": "Retail / grocery", "town": "Chilenje, Lusaka", "province": "Lusaka",
                    "years_trading": 4, "employees": 2, "pacra_number": "",
                    "description": "Grocery shop selling mealie meal, cooking oil and household goods to the "
                                   "Chilenje community. Open 7 days a week."},
        "loan": {"requested_zmw": 10_000, "term_months": 12, "purpose": "Stock purchase"},
    },
    "new": {
        "button": "New tailor (receipts only)",
        "icon": ":material/content_cut:",
        "help": "Only pasted messages, no wallet connected - Absa policy blocks the loan",
        "business_name": "Kanyama Tailoring",
        "phone": "+260971112233",
        "links": [],
        "messages": SAMPLE_DATA,
        "profile": {"owner_name": "Chanda Phiri", "owner_role": "Owner", "owner_email": "",
                    "sector": "Tailoring & clothing", "town": "Kanyama, Lusaka", "province": "Lusaka",
                    "years_trading": 1, "employees": 0, "pacra_number": "",
                    "description": "Tailoring school uniforms and chitenge outfits from a market stall."},
        "loan": {"requested_zmw": 5_000, "term_months": 6, "purpose": "Equipment"},
    },
}


def load_demo(kind):
    """Fill in the whole form for a demo applicant, as if they had verified and linked their accounts."""
    demo = DEMO_BUSINESSES[kind]
    st.session_state.business_name = demo["business_name"]
    st.session_state.consent_given = True
    st.session_state.connection = make_connection(demo["business_name"], demo["phone"], "SMS OTP (demo)")
    st.session_state.linked, st.session_state.synced = {}, {}
    for provider, phone in demo["links"]:
        phone = phone or demo["phone"]
        link_account(provider, mask_phone(phone), hashlib.sha256(phone.encode()).hexdigest(),
                     NETWORK_BY_PREFIX[phone[4:6]],
                     "Primary number (verified at sign-up)" if phone == demo["phone"] else "Own SMS code (demo)")
    st.session_state.messy_data = demo["messages"]
    for field, value in demo["profile"].items():
        st.session_state[f"profile_{field}"] = value
    for key, value in demo["loan"].items():
        st.session_state[key] = value
    st.session_state.toast = f"Demo loaded: {demo['business_name']}. Check the details, then submit."


@st.dialog("Link an account")
def link_account_dialog(source):
    connection = st.session_state.connection
    required = PROVIDER_NETWORK[source]
    already = {a["phone_sha256"] for a in accounts_for(source).values()}
    st.markdown(f"#### {source}")
    st.caption(LINK_HELP[source])

    if (required is None or connection["network"] == required) and connection["phone_sha256"] not in already:
        if st.button(f"Use my verified number {connection['phone_masked']}", type="primary", width="stretch",
                     icon=":material/verified_user:"):
            link_account(source, connection["phone_masked"], connection["phone_sha256"], connection["network"],
                         "Primary number (verified at sign-up)")
            st.rerun()
        st.markdown("**Or link a different number**")
    elif required and connection["network"] != required:
        st.info(f"Your verified number is on {connection['network']}. Enter your {required} number - "
                "we'll send it a code to prove it's yours.", icon=":material/info:")

    raw = st.text_input("Mobile number", key=f"link_phone_{source}", placeholder="e.g. 097 123 4567")
    if st.button("Send code to this number", icon=":material/sms:", key=f"link_send_{source}"):
        phone = normalise_phone(raw)
        if phone is None:
            st.error(invalid_phone_message(raw))
        elif hashlib.sha256(phone.encode()).hexdigest() in already:
            st.error(f"{mask_phone(phone)} is already linked to {source}.")
        elif required and NETWORK_BY_PREFIX[phone[4:6]] != required:
            article = "an" if required[0] in "AEIOU" else "a"
            st.error(f"That is {'an' if NETWORK_BY_PREFIX[phone[4:6]][0] in 'AEIOU' else 'a'} "
                     f"{NETWORK_BY_PREFIX[phone[4:6]]} number. {source} needs {article} {required} number.")
        else:
            st.session_state.link_otp = {"source": source, "phone": phone, "sent_at": time.time(),
                                         "attempts": 0, "code": f"{secrets.randbelow(10**6):06d}"}

    pending = st.session_state.get("link_otp")
    if pending and pending["source"] == source:
        st.html(f'<div class="cg-sms">Absa: your code to link {source} is {pending["code"]}. '
                f'(Simulated SMS to {mask_phone(pending["phone"])})</div>')
        code_col, btn_col = st.columns([3, 2], vertical_alignment="bottom")
        entered = code_col.text_input("6-digit code", key=f"link_code_{source}", max_chars=6)
        if btn_col.button("Verify & link", type="primary", width="stretch", key=f"link_verify_{source}"):
            if time.time() - pending["sent_at"] > OTP_TTL_SECONDS:
                del st.session_state.link_otp
                st.error("The code has expired. Send a new one.")
            elif hmac.compare_digest(entered.strip(), pending["code"]):
                phone = pending["phone"]
                del st.session_state.link_otp
                link_account(source, mask_phone(phone), hashlib.sha256(phone.encode()).hexdigest(),
                             NETWORK_BY_PREFIX[phone[4:6]], "Own SMS code")
                st.rerun()
            else:
                pending["attempts"] += 1
                if pending["attempts"] >= MAX_OTP_ATTEMPTS:
                    del st.session_state.link_otp
                    st.error("Too many incorrect attempts. Send a new code.")
                else:
                    st.error(f"Incorrect code. {MAX_OTP_ATTEMPTS - pending['attempts']} attempt(s) left.")
