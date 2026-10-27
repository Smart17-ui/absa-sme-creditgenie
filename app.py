import os
import re
import io
import html
import hmac
import json
import time
import uuid
import random
import secrets
import hashlib
import datetime
from typing import Literal

import altair as alt
import pandas as pd
import PIL.Image
import streamlit as st
from dotenv import load_dotenv
from google import genai
from pydantic import BaseModel, Field, ValidationError

# 1. Load the API key from the .env file
load_dotenv()
api_key = os.getenv("GEMINI_API_KEY")
MODEL_NAME = os.getenv("GEMINI_MODEL", "gemini-3.5-flash-lite")

st.set_page_config(page_title="Absa SME CreditGenie", layout="wide")

if not api_key:
    st.error("API Key not found. Please check your .env file.")
    st.stop()

# 2. Initialize the Gemini Client (cached so it isn't rebuilt on every rerun)
@st.cache_resource
def get_client(key):
    return genai.Client(api_key=key)

client = get_client(api_key)

PERSONAS = ("Strict Risk Manager", "Financial Growth Coach", "Synthetic Data Engineer")

SAMPLE_DATA = (
    "Mon: Received 4,000 ZMW from Mama Chileshe for 2 bags of mealie meal.\n"
    "Mon: Paid 1,500 ZMW for transport to Lusaka.\n"
    "Tue: Received 2,000 ZMW from Mr. Phiri via MTN MoMo, ref 0971234567.\n"
    "Wed: Paid 800 ZMW to supplier for cooking oil.\n"
    "Thu: Received 1,200 ZMW from Bashi Mwansa.\n"
    "Fri: I still owe the supplier 1,000 ZMW, will pay next week."
)

# Simulated provider feeds - stand-ins for the MTN MoMo, Airtel Money and WhatsApp Business APIs.
# The WhatsApp chat deliberately overlaps the MoMo record so the agent has to de-duplicate.
SIMULATED_FEEDS = {
    "MTN MoMo": (
        "MTN MoMo: You have received ZMW 4,000.00 from MAMA CHILESHE (0961234567) on 20/10/2026 09:12. Txn ID 8812734. Bal ZMW 5,240.00\n"
        "MTN MoMo: You have sent ZMW 1,500.00 to KALULU TRANSPORT (0966554433) on 20/10/2026 11:40. Txn ID 8812901. Bal ZMW 3,740.00\n"
        "MTN MoMo: You have received ZMW 2,000.00 from JOSEPH PHIRI (0767889900) on 21/10/2026 15:03. Txn ID 8813550. Bal ZMW 5,740.00\n"
        "MTN MoMo: Payment of ZMW 350.00 to ZESCO prepaid electricity on 22/10/2026 08:05. Txn ID 8814002. Bal ZMW 5,390.00"
    ),
    "Airtel Money": (
        "Airtel Money: You have received K1,200.00 from BASHI MWANSA 0977123456 on 23/10/2026. TID: PP261023.1422.A55\n"
        "Airtel Money: You have paid K650.00 for FUEL at Merchant 40213 on 23/10/2026. TID: PP261023.1610.C12\n"
        "Airtel Money: You have sent K800.00 to MWILA WHOLESALERS 0975432100 on 24/10/2026. TID: PP261024.0931.D07"
    ),
    "WhatsApp": (
        "[20/10/2026, 08:55] Mama Chileshe: Hello, I've sent the 4,000 on MoMo for the 2 bags of mealie meal\n"
        "[21/10/2026, 19:40] Mama Chileshe: Are you coming to the kitchen party on Saturday?\n"
        "[22/10/2026, 17:30] Mwila Wholesalers: Reminder: balance of K1,000 for the cooking oil is still outstanding, please pay by Friday\n"
        "[23/10/2026, 07:15] Grace Banda: Good morning! How is your mother feeling today?\n"
        "[24/10/2026, 12:10] Grace Banda: Can you deliver 3 bags tomorrow? I'll pay K3,000 cash on delivery\n"
        "[25/10/2026, 10:02] Grace Banda: Received the bags, I've paid you the K3,000 cash. Thank you!"
    ),
}
SOURCES = (*SIMULATED_FEEDS, "Pasted text", "Image")
VERIFIED_SOURCES = {"MTN MoMo", "Airtel Money"}

# Which network a linked number must be on (None = any network)
PROVIDER_NETWORK = {"MTN MoMo": "MTN", "Airtel Money": "Airtel", "WhatsApp": None}
LINK_HELP = {
    "MTN MoMo": "Use the MTN number (096 or 076) that your MoMo wallet is registered to.",
    "Airtel Money": "Use the Airtel number (097 or 077) that your Airtel Money wallet is registered to.",
    "WhatsApp": "Use the number of your WhatsApp Business account. Only messages about money are shared - "
                "personal messages are removed before anything leaves this portal.",
}

# Chat messages are only shared when they are about money (data minimisation)
CHAT_PREFIX_RE = re.compile(r"^\[[^\]]*\]\s*[^:]{1,60}:\s*")  # "[20/10/2026, 08:55] Name: "
MONEY_RE = re.compile(
    r"\b(?:zmw|kwacha|k\s?\d[\d,]*|paid|pay|payment|sent|send|received|receive|owe|owed|owing|invoice|"
    r"balance|deposit|loan|debt|credit|cash|bought|sold|price|cost|order|supplier)\b|\d{3,}",
    re.IGNORECASE,
)


def keep_money_messages(text):
    """Keep only lines about money; return (kept text, number of messages removed)."""
    lines = [line for line in text.splitlines() if line.strip()]
    kept = [line for line in lines if MONEY_RE.search(CHAT_PREFIX_RE.sub("", line))]
    return "\n".join(kept), len(lines) - len(kept)


MAX_NUMBERS_PER_PROVIDER = 3
SAMPLE_CUSTOMERS = ("CHANDA MULENGA", "PRISCA BANDA", "MUSONDA PHIRI", "NATASHA ZULU", "KELVIN TEMBO", "ESTHER MWALE")
SAMPLE_SUPPLIERS = ("LUSAKA FRESH PRODUCE", "KAFUE MILLING", "COMESA MARKET TRADERS")


def simulated_feed(provider, seed):
    """Sample history for an extra linked number. Seeded by the number, so it is stable and unique."""
    rng = random.Random(seed)
    days = sorted(rng.sample(range(18, 27), 4))
    amounts = [rng.randrange(400, 3200, 50) for _ in range(4)]
    customers = rng.sample(SAMPLE_CUSTOMERS, 3)
    supplier = rng.choice(SAMPLE_SUPPLIERS)
    if provider == "WhatsApp":
        return "\n".join([
            f"[{days[0]}/10/2026, 09:{rng.randrange(10, 59)}] {customers[0].title()}: I've sent K{amounts[0]:,} for the order",
            f"[{days[1]}/10/2026, 18:{rng.randrange(10, 59)}] {customers[1].title()}: Happy Sunday! See you at church",
            f"[{days[2]}/10/2026, 11:{rng.randrange(10, 59)}] {supplier.title()}: Your invoice of K{amounts[3]:,} "
            "for stock is due on Friday",
        ])
    name, cur = ("MTN MoMo", "ZMW ") if provider == "MTN MoMo" else ("Airtel Money", "K")
    lines = [f"{name}: You have received {cur}{amounts[i]:,.2f} from {customers[i]} on {days[i]}/10/2026. "
             f"Txn ID {rng.randrange(10**7, 10**8)}" for i in range(3)]
    lines.append(f"{name}: You have sent {cur}{amounts[3]:,.2f} to {supplier} on {days[3]}/10/2026. "
                 f"Txn ID {rng.randrange(10**7, 10**8)}")
    return "\n".join(lines)


# 3. Strict output schema - the model must return exactly this shape
class Transaction(BaseModel):
    date: str
    description: str
    type: Literal["Income", "Expense", "Debt"]
    amount_zmw: float = Field(ge=0)
    source: Literal[SOURCES]


class CreditAnalysis(BaseModel):
    synthetic_transactions: list[Transaction]
    credit_score: int = Field(ge=1, le=100)
    loan_recommendation_zmw: float = Field(ge=0)
    risk_summary: str


OUTPUT_RULES = """
Classify every transaction as one of:
- "Income": money received by the business.
- "Expense": money already paid out by the business.
- "Debt": money the business still owes (e.g. "I owe the supplier"). Do not also list it as an Expense.
All amounts are positive numbers in ZMW. If a date is not given, use the day mentioned or "Unknown".
Do not calculate totals - they are computed by the bank's system from your transaction list.
Only use figures that appear in the data. Never invent transactions.
Orders, quotes or promises to pay in the future are NOT income until the data shows the money was paid.

The data may come from several labelled sources. Set "source" on every transaction to the label of the
section it came from ("MTN MoMo", "Airtel Money", "WhatsApp", "Pasted text" or "Image").
The same payment often appears twice, e.g. a WhatsApp message "I've sent the 4,000" and the matching
MoMo SMS. List each real payment ONCE: when amount, counterparty and date match, keep only the
mobile-money record, because it is the verified one.
The business may link several of its own wallets. Money moved between its own accounts (e.g. a
transfer from its MTN wallet to its own Airtel wallet) is NOT income or expense - leave it out.
"""

PERSONA_INSTRUCTIONS = {
    "Strict Risk Manager": """
    You are a strict, highly experienced Senior Credit Risk Manager at Absa Bank Zambia.
    Your job is to protect the bank from bad loans while still supporting Zambian SMEs.

    Analyze the text/image to find all income, expenses and outstanding debts.
    - If the business is making a profit after debts, calculate a safe loan amount (max 3x their profit).
    - If expenses plus debts are higher than the income, the credit score must be below 40 and the loan recommendation must be 0.
    - "risk_summary": 2 sentences explaining the risk level and why you approved or denied the loan.
    """,
    "Financial Growth Coach": """
    You are an AI Financial Inclusion Officer at Absa Bank Zambia.
    Your goal is to help informal SME business owners formalize their business and access capital to grow.

    Assume the data provided represents a typical week of business. Annualize the weekly profit to estimate their yearly cash flow.
    Give a highly encouraging but realistic credit score based on their hustle and cash flow.
    - "risk_summary": 2 sentences explaining their financial health and how Absa can help them grow.
    """,
    "Synthetic Data Engineer": """
    You are an AI Data Engineer for Absa Bank. Your job is to protect customer privacy (Zambia Data Protection Act 2021 / POPIA) while extracting valuable credit data.

    Step 1: Names in the text have already been masked as "Customer A", "Customer B", etc. Keep those labels.
            If you see any other real person's name (especially in an image), replace it with the next "Customer" label.
            Descriptions must contain NO real names, phone numbers or ID numbers.
    Step 2: Extract the financial transactions.
    Step 3: Act as a credit engine and score the anonymous data.
    - "risk_summary": 2 sentences explaining the data privacy measures taken and the credit decision.
    """,
}


# 4. Local PII redaction - runs BEFORE anything is sent to the AI
# Names may be "Mama Chileshe" (chat) or "MAMA CHILESHE" (mobile-money SMS), so allow all-caps words
NAME = r"[A-Z][A-Za-z]+(?:\s+[A-Z][A-Za-z]+){0,2}"
PHONE_RE = re.compile(r"(?:\+?260[\s-]?|\b0)[79]\d(?:[\s-]?\d){7}\b")
NRC_RE = re.compile(r"\b\d{6}/\d{2}/\d\b")
HONORIFIC_NAME_RE = re.compile(rf"\b(?i:Mama|Bashi|Ba|Mr|Mrs|Ms|Dr|Madam)\.?\s+{NAME}")
PARTY_NAME_RE = re.compile(rf"\b((?:from|to|by)\s+)({NAME})")
WHATSAPP_SENDER_RE = re.compile(rf"^(\[[^\]]+\]\s*)({NAME})(?=:)", re.MULTILINE)
NOT_A_NAME = {
    "Customer", "Supplier", "Vendor", "Landlord", "Transport", "Market", "Bank",
    "Absa", "Mobile", "Money", "Airtel", "Zamtel", "Zesco", "Shoprite", "Cash",
    "Mtn", "Momo", "Merchant", "Fuel", "You", "Me",
    "Lusaka", "Ndola", "Kitwe", "Livingstone", "Kabwe", "Chipata", "Solwezi",
    "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday",
    "January", "February", "March", "April", "May", "June", "July", "August",
    "September", "October", "November", "December",
}


def redact(text, mask_names, name_map):
    """Mask phone/NRC numbers always, and personal names when mask_names is True.
    name_map keeps labels consistent ("Mama Chileshe" -> "Customer A" everywhere)."""
    count = 0

    def label_for(name):
        name = name.title()  # "MAMA CHILESHE" and "Mama Chileshe" share one label
        if name not in name_map:
            name_map[name] = f"Customer {chr(ord('A') + len(name_map) % 26)}"
        return name_map[name]

    text, n = PHONE_RE.subn("[PHONE REDACTED]", text)
    count += n
    text, n = NRC_RE.subn("[NRC REDACTED]", text)
    count += n

    if mask_names:
        # Names seen before (e.g. in another source) are replaced wherever they appear
        for name, label in list(name_map.items()):
            text, n = re.subn(re.escape(name), label, text, flags=re.IGNORECASE)
            count += n

        def sub_honorific(m):
            nonlocal count
            count += 1
            return label_for(m.group(0))

        def sub_named_group(m):
            nonlocal count
            name = m.group(2)
            if name.split()[0].title() in NOT_A_NAME:
                return m.group(0)
            count += 1
            return f"{m.group(1)}{label_for(name)}"

        text = HONORIFIC_NAME_RE.sub(sub_honorific, text)
        text = WHATSAPP_SENDER_RE.sub(sub_named_group, text)
        text = PARTY_NAME_RE.sub(sub_named_group, text)

    return text, count


# 5. Deterministic credit policy - the AI proposes, the bank's rules decide
def apply_policy(persona, analysis, totals, requested_zmw=None):
    score = analysis.credit_score
    loan = analysis.loan_recommendation_zmw
    notes = []
    free_cash = totals["net_cash_flow"] - totals["outstanding_debt"]

    if free_cash <= 0 and loan > 0:
        notes.append("Loan set to ZMW 0: expenses plus debts meet or exceed income.")
        loan = 0
    if persona == "Strict Risk Manager":
        if free_cash <= 0 and score >= 40:
            notes.append(f"Score capped at 39 (AI proposed {score}): negative cash flow after debts.")
            score = 39
        if free_cash > 0 and loan > 3 * free_cash:
            notes.append(f"Loan capped at 3x free cash flow (AI proposed ZMW {loan:,.0f}).")
            loan = 3 * free_cash
    if requested_zmw and loan > requested_zmw:
        notes.append(f"Loan limited to the ZMW {requested_zmw:,.0f} the business applied for.")
        loan = requested_zmw

    return score, round(loan, 2), notes


def compute_totals(transactions):
    def total(kind):
        return round(sum(t.amount_zmw for t in transactions if t.type == kind), 2)

    income, expense, debt = total("Income"), total("Expense"), total("Debt")
    verified_income = sum(
        t.amount_zmw for t in transactions if t.type == "Income" and t.source in VERIFIED_SOURCES
    )
    return {
        "total_income": income,
        "total_expense": expense,
        "outstanding_debt": debt,
        "net_cash_flow": round(income - expense, 2),
        # Share of income backed by a mobile-money record rather than a chat message or claim
        "verified_income_pct": round(100 * verified_income / income) if income else 0,
    }


# 6. Simulated account verification (SMS OTP + consent)
CONSENT_TEXT = (
    "The business owner consents to Absa Bank Zambia accessing their MTN MoMo, Airtel Money and "
    "WhatsApp Business transaction history for this credit assessment only, in line with the "
    "Data Protection Act No. 3 of 2021. Consent can be revoked at any time."
)
OTP_TTL_SECONDS = 300
MAX_OTP_ATTEMPTS = 3
ZAMBIAN_MOBILE_RE = re.compile(r"^(?:\+?260|0)((?:9[5-7]|7[5-7])\d{7})$")
NETWORK_BY_PREFIX = {"96": "MTN", "76": "MTN", "97": "Airtel", "77": "Airtel", "95": "Zamtel", "75": "Zamtel"}
MAX_IMAGES_PER_RUN = 5
ROLES = ("SME Portal", "Absa Analyst")
LOAN_PURPOSES = ("Stock purchase", "Equipment", "Business expansion", "Working capital", "Other")


def normalise_phone(raw):
    match = ZAMBIAN_MOBILE_RE.match(re.sub(r"[\s()-]", "", raw))
    return f"+260{match.group(1)}" if match else None


def mask_phone(phone):
    return f"{phone[:4]} {phone[4:6]} *** {phone[-4:]}"


def now_iso():
    return datetime.datetime.now().astimezone().isoformat(timespec="seconds")


def pretty_time(iso):
    return datetime.datetime.fromisoformat(iso).strftime("%d %b %Y, %H:%M")


def flash(kind, message):
    st.session_state.flash = (kind, message)


def send_otp():
    phone = normalise_phone(st.session_state.phone_input)
    if not st.session_state.business_name.strip():
        flash("error", "Enter the registered business name.")
    elif phone is None:
        flash("error", "Enter a valid Zambian mobile number, e.g. 097 123 4567.")
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
        phone = otp["phone"]
        st.session_state.connection = {
            "business_name": st.session_state.business_name.strip(),
            "phone_masked": mask_phone(phone),
            "phone_sha256": hashlib.sha256(phone.encode()).hexdigest(),
            "network": NETWORK_BY_PREFIX[phone[4:6]],
            "verification": "SMS OTP (simulated)",
            "consent_text": CONSENT_TEXT,
            "verified_at": now_iso(),
        }
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
            st.error("Enter a valid Zambian mobile number, e.g. 097 123 4567.")
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


# 7. Application + case file. The application is what the SME submits; the case is the
# agent's memory of everything it has already analysed for that application.
def empty_case():
    return {
        "case_id": f"CASE-{uuid.uuid4().hex[:6].upper()}",
        "opened_at": now_iso(),
        "ledger": [],     # every transaction found so far (already redacted)
        "processed": {},  # input fingerprint -> label, so the same evidence is never counted twice
        "name_map": {},   # real name -> "Customer X"; kept in memory only, never exported
        "history": [],    # one entry per assessment run
    }


def start_new_case():
    st.session_state.case = empty_case()
    st.session_state.application = None
    st.session_state.report = None
    st.session_state.connection = None
    st.session_state.linked = {}
    st.session_state.synced = {}
    st.session_state.messy_data = ""
    st.session_state.uploader_key += 1  # clears the file uploader


# --- SESSION STATE (survives Streamlit reruns) ---
for key, default in {"messy_data": "", "synced": {}, "linked": {}, "connection": None, "report": None,
                     "uploader_key": 0, "application": None, "session_started": now_iso()}.items():
    st.session_state.setdefault(key, default)
if "case" not in st.session_state:
    st.session_state.case = empty_case()
case = st.session_state.case
application = st.session_state.application


# 8. Look & feel - Absa-branded header, step cards, KPI tiles and sidebar
st.html("""
<style>
  [data-testid="stAppDeployButton"], #MainMenu, footer { display: none; }
  .block-container, [data-testid="stMainBlockContainer"] { padding-top: 3.2rem; max-width: 1280px; }
  .cg-hero {
    background: linear-gradient(115deg, #870A3C 0%, #DC0032 60%, #F0325A 100%);
    color: #fff; border-radius: 16px; padding: 24px 32px; margin-bottom: 4px;
  }
  .cg-hero.cg-analyst { background: linear-gradient(115deg, #2B1A22 0%, #5C0F2E 55%, #A8123E 100%); }
  .cg-hero-role { font-size: 0.75rem; letter-spacing: 0.1em; text-transform: uppercase; opacity: 0.85; }
  .cg-hero h1 { color: #fff; font-size: 1.85rem; font-weight: 700; letter-spacing: -0.02em; margin: 2px 0 0; padding: 0; }
  .cg-hero p { color: rgba(255,255,255,0.9); margin: 6px 0 0; font-size: 1.0rem; }
  .cg-chip {
    display: inline-block; margin: 14px 8px 0 0; padding: 3px 12px; border-radius: 999px;
    background: rgba(255,255,255,0.15); border: 1px solid rgba(255,255,255,0.3); font-size: 0.78rem;
  }
  .cg-step { display: flex; align-items: center; gap: 10px; font-size: 1.08rem; font-weight: 600; }
  .cg-step-num {
    display: inline-flex; align-items: center; justify-content: center; width: 26px; height: 26px;
    border-radius: 50%; background: #DC0032; color: #fff; font-size: 0.85rem;
  }
  .cg-sub { color: #6B6B6B; font-size: 0.9rem; margin: 2px 0 6px 36px; }
  .cg-sms {
    background: #F6F1F2; border-left: 3px solid #DC0032; border-radius: 8px;
    padding: 10px 14px; font-family: ui-monospace, Consolas, monospace; font-size: 0.88rem;
  }

  /* Application progress tracker */
  .cg-track { display: flex; gap: 6px; margin: 8px 0 4px; flex-wrap: wrap; }
  .cg-track-step { flex: 1 1 120px; border-top: 4px solid #E6DFE1; padding-top: 6px; font-size: 0.82rem; color: #8A8A8A; }
  .cg-track-step.done { border-color: #1F8A4C; color: #1E1E1E; }
  .cg-track-step.now { border-color: #DC0032; color: #1E1E1E; font-weight: 600; }

  /* KPI tiles: wrap onto a new row instead of truncating on narrow screens */
  .cg-kpis { display: grid; grid-template-columns: repeat(auto-fit, minmax(190px, 1fr)); gap: 12px; margin: 6px 0 4px; }
  .cg-kpi { border: 1px solid #E6DFE1; border-radius: 12px; padding: 14px 16px; background: #fff; min-width: 0; }
  .cg-kpi-label { color: #5E5E5E; font-size: 0.85rem; }
  .cg-kpi-value { font-size: 1.7rem; font-weight: 700; color: #1E1E1E; line-height: 1.25; margin-top: 4px; white-space: nowrap; }
  .cg-unit { font-size: 0.95rem; font-weight: 500; color: #6B6B6B; margin-left: 2px; }
  .cg-cur { font-size: 0.8rem; font-weight: 600; color: #6B6B6B; margin-right: 5px; letter-spacing: 0.03em; }
  .cg-kpi-note { color: #6B6B6B; font-size: 0.8rem; margin-top: 6px; }
  .cg-meter { position: relative; height: 6px; border-radius: 3px; background: #EEE8EA; margin-top: 10px; }
  .cg-meter-fill { height: 6px; border-radius: 3px; }
  .cg-meter-tick { position: absolute; top: -2px; width: 2px; height: 10px; background: #fff; }

  /* Sidebar */
  .cg-side-brand { font-weight: 700; font-size: 1.1rem; color: #1E1E1E; }
  .cg-side-brand span { color: #DC0032; }
  .cg-side-tag { color: #6B6B6B; font-size: 0.8rem; margin-bottom: 4px; }
  .cg-side-card { background: #fff; border: 1px solid #E6DFE1; border-radius: 12px; padding: 12px 14px; }
  .cg-side-label { font-size: 0.7rem; letter-spacing: 0.08em; text-transform: uppercase; color: #7A7A7A; margin-bottom: 6px; font-weight: 600; }
  .cg-row { display: flex; justify-content: space-between; gap: 8px; font-size: 0.86rem; padding: 3px 0; }
  .cg-row span:first-child { color: #6B6B6B; }
  .cg-row span:last-child { font-weight: 600; text-align: right; overflow-wrap: anywhere; }
  .cg-side-foot { color: #8A8A8A; font-size: 0.72rem; line-height: 1.4; }
</style>
""")


def hero(role_label, title, tagline, chips, analyst=False):
    st.html(f'<div class="cg-hero{" cg-analyst" if analyst else ""}"><div class="cg-hero-role">{role_label}</div>'
            f'<h1>{title}</h1><p>{tagline}</p>'
            + "".join(f'<span class="cg-chip">{c}</span>' for c in chips) + "</div>")


def step_header(number, title, subtitle):
    st.html(f'<div class="cg-step"><span class="cg-step-num">{number}</span>{title}</div>'
            f'<div class="cg-sub">{subtitle}</div>')


def fingerprint(data):
    return hashlib.sha256(data).hexdigest()


STATUS_BADGE = {
    "Submitted": ("blue", ":material/send:"),
    "Under review": ("orange", ":material/manage_search:"),
    "More info requested": ("orange", ":material/help:"),
    "Approved": ("green", ":material/check_circle:"),
    "Declined": ("red", ":material/cancel:"),
}


def status_badge(status):
    color, icon = STATUS_BADGE[status]
    st.badge(status, icon=icon, color=color)


flash_msg = st.session_state.pop("flash", None)
if "toast" in st.session_state:
    st.toast(st.session_state.pop("toast"), icon=":material/task_alt:")

# --- ROLE SWITCH (a demo stand-in for login: each role sees only its own view) ---
with st.sidebar:
    st.html('<div class="cg-side-brand">Credit<span>Genie</span></div>'
            '<div class="cg-side-tag">Absa SME lending assistant</div>')
    role = st.segmented_control("Viewing as", ROLES, default=ROLES[0], required=True, key="role",
                                width="stretch")


# ==========================================================================
# VIEW 1: SME PORTAL - the business owner verifies, shares data and applies
# ==========================================================================
def application_tracker(app):
    stages = ["Submitted", "Under review", "Decision"]
    current = {"Submitted": 0, "Under review": 1, "More info requested": 1}.get(app["status"], 3)
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
        "Your records are used to assess this loan and are not kept after your session ends.\n\n"
        ":material/person: **A person makes the decision**  \n"
        "Our AI prepares a report, but an Absa credit analyst reviews every application and makes the final decision."
    )
    st.caption(CONSENT_TEXT)


def sme_portal():
    hero("Business owner", "Absa SME Portal",
         "Apply for working capital by securely sharing your mobile money, WhatsApp and receipts. "
         "No paperwork, no branch visit.", [])

    with st.container(border=True, horizontal=True, vertical_alignment="center"):
        st.markdown(":material/lock: **Your data is protected.** You choose what to share, your customers' "
                    "details are hidden from our AI, and an Absa analyst makes the final decision.")
        if st.button("How we protect your data", icon=":material/info:", type="tertiary"):
            privacy_dialog()

    app = application
    if app:
        with st.container(border=True):
            head_col, badge_col = st.columns([3, 1], vertical_alignment="center")
            head_col.markdown(f"**Application {app['ref']}** · ZMW {app['requested_zmw']:,.0f} for "
                              f"{app['purpose'].lower()}")
            head_col.caption(f"Submitted {pretty_time(app['submitted_at'])} · last update {pretty_time(app['updated_at'])}")
            with badge_col.container(horizontal=True, horizontal_alignment="right"):
                status_badge(app["status"])
            application_tracker(app)

            decision = app["decision"]
            if app["status"] == "Approved":
                st.success(f"Good news! Absa can offer you **ZMW {decision['amount_zmw']:,.0f}**. "
                           "A relationship manager will contact you to finalise the loan.", icon=":material/celebration:")
            elif app["status"] == "Declined":
                st.error("We are unable to offer a loan at this time. You are welcome to apply again "
                         "once your business has more transaction history.", icon=":material/info:")
            elif app["status"] == "More info requested":
                st.warning(f"**Absa needs more information:** {decision['note']}  \n"
                           "Add the documents below and send them to Absa.", icon=":material/help:")
            else:
                st.info("Thank you - your application is with an Absa credit analyst. "
                        "You can still send extra documents below.", icon=":material/hourglass_top:")
            if decision and decision.get("note") and app["status"] in ("Approved", "Declined"):
                st.caption(f"Message from Absa: {decision['note']}")

        if app["status"] in ("Approved", "Declined"):
            st.caption("To make a new application, use **Start new application** in the sidebar.")
            return

    # --- STEP 1: VERIFY ---
    connection = st.session_state.connection
    with st.container(border=True):
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
        if flash_msg:
            kind, message = flash_msg
            if kind == "sms":
                st.html(f'<div class="cg-sms">{html.escape(message)}</div>')
            else:
                getattr(st, kind)(message)

    # --- STEP 2: SHARE DATA ---
    with st.container(border=True):
        step_header(2, "Share your business records",
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

    # --- STEP 3: APPLY ---
    with st.container(border=True):
        if app:
            step_header(3, "Send more documents", "Anything new is added to your existing application.")
        else:
            step_header(3, "Apply", "Tell us how much you need. An Absa credit analyst reviews every application.")
            amount_col, purpose_col = st.columns(2)
            amount_col.number_input("Amount needed (ZMW)", min_value=500, max_value=500_000, value=10_000,
                                    step=500, key="requested_zmw")
            purpose_col.selectbox("What is the loan for?", LOAN_PURPOSES, key="purpose")

        with st.container(horizontal=True):
            for e in unsent:
                st.badge(f"Ready · {e['label']}", icon=":material/upload:", color="blue")
            for e in (app["evidence"] if app else []):
                st.badge(f"Sent · {e['label']}", icon=":material/check:", color="gray")
        if unsent_images > MAX_IMAGES_PER_RUN:
            st.warning(f"Please send at most {MAX_IMAGES_PER_RUN} images at a time.")
        if connection is None:
            st.caption("Verify your business number in step 1 to continue.")

        clicked = st.button("Send to Absa" if app else "Submit application", type="primary",
                            icon=":material/send:", width="stretch",
                            disabled=connection is None or not unsent or unsent_images > MAX_IMAGES_PER_RUN)
    if clicked:
        stamp = now_iso()
        if app:
            app["evidence"] += unsent
            app["linked_accounts"].update(st.session_state.linked)
            app["status"] = "Under review" if app["status"] == "More info requested" else app["status"]
            app["updated_at"] = stamp
            st.session_state.toast = f"{len(unsent)} new document(s) sent to Absa"
        else:
            st.session_state.application = {
                "ref": f"APP-{uuid.uuid4().hex[:6].upper()}",
                "business_name": connection["business_name"],
                "requested_zmw": float(st.session_state.requested_zmw),
                "purpose": st.session_state.purpose,
                "status": "Submitted",
                "submitted_at": stamp,
                "updated_at": stamp,
                "evidence": unsent,
                "linked_accounts": dict(st.session_state.linked),
                "decision": None,
            }
            st.session_state.toast = "Application submitted. Switch to Absa Analyst to review it."
        st.rerun()


# ==========================================================================
# VIEW 2: ABSA ANALYST - AI prepares the assessment, the analyst decides
# ==========================================================================
def run_analysis(persona, case, app, new_items, log):
    """Analyse only the evidence that is new to the case, then re-score the whole case.
    log(step) records each agent step for the live status panel and the audit trail."""
    mask_names = persona == "Synthetic Data Engineer"
    redactions = 0
    trace = []

    def step(message):
        trace.append(message)
        log(message)

    contents = [
        PERSONA_INSTRUCTIONS[persona] + OUTPUT_RULES,
        f"The business applied for ZMW {app['requested_zmw']:,.0f} for {app['purpose'].lower()}. "
        "Judge whether that amount is affordable from the cash flow.",
    ]

    if case["ledger"]:
        known = [{k: t[k] for k in Transaction.model_fields} for t in case["ledger"]]
        contents.append(
            "CASE FILE - transactions already recorded from earlier evidence. They are already counted: "
            "do NOT repeat them in synthetic_transactions, but DO use them when scoring.\n"
            + json.dumps(known, indent=1)
        )
    if case["history"]:
        last = case["history"][-1]
        contents.append(
            f"Previous decision on this case: credit score {last['credit_score']}, loan ZMW "
            f"{last['recommended_loan_zmw']:,.0f} ({last['persona']}). If your decision changes, "
            "say in risk_summary what changed and why."
        )

    if new_items:
        contents.append("NEW EVIDENCE to add to the case:")
    else:
        contents.append("There is no new evidence. Re-assess the case file and return an empty synthetic_transactions list.")
    for item in new_items:
        if "text" in item:
            clean_text, n = redact(item["text"], mask_names, case["name_map"])
            redactions += n
            contents.append(f'Source "{item["source"]}":\n{clean_text}')
        else:
            contents.append(f'Source "Image" ({item["label"]}):')
            contents.append(PIL.Image.open(io.BytesIO(item["image"])))

    personal_removed = sum(e.get("filtered_out", 0) for e in new_items)
    if personal_removed:
        step(f"{personal_removed} personal (non-financial) chat message(s) were removed in the SME Portal "
             "and never shared")
    masked_what = "phone numbers, NRC numbers and names" if mask_names else "phone and NRC numbers"
    step(f"Masked {redactions} personal identifier(s) locally ({masked_what}) before calling the AI")
    if new_items:
        step(f"Added {len(new_items)} new item(s): {', '.join(e['label'] for e in new_items)}")
    if case["ledger"]:
        step(f"Recalled {len(case['ledger'])} transaction(s) and {len(case['history'])} earlier "
             f"assessment(s) from case {case['case_id']}")

    response = client.models.generate_content(
        model=MODEL_NAME,
        contents=contents,
        config={
            "response_mime_type": "application/json",
            "response_schema": CreditAnalysis,
            "temperature": 0.2,
        },
    )
    st.session_state.raw_response = response.text
    analysis = CreditAnalysis.model_validate_json(response.text)
    step(f"{persona} extracted {len(analysis.synthetic_transactions)} new transaction(s) "
         f"with {MODEL_NAME}; output passed schema validation")

    # Second privacy pass on what came back (catches PII the AI read from an image)
    for t in analysis.synthetic_transactions:
        t.description, n = redact(t.description, mask_names, case["name_map"])
        redactions += n

    audit_id = f"CG-{uuid.uuid4().hex[:10].upper()}"
    ledger = case["ledger"] + [{**t.model_dump(), "added_by": audit_id} for t in analysis.synthetic_transactions]
    totals = compute_totals([Transaction(**t) for t in ledger])
    step(f"Rules engine computed totals over all {len(ledger)} transaction(s) in the case")
    score, loan, notes = apply_policy(persona, analysis, totals, app["requested_zmw"])
    step(f"Absa credit policy applied: {len(notes)} guardrail(s) triggered")
    step(f"Audit report {audit_id} generated - awaiting analyst decision")

    generated_at = now_iso()
    previous = list(case["history"])
    case["ledger"] = ledger
    case["processed"].update({e["fp"]: e["label"] for e in new_items})
    case["history"].append({
        "audit_id": audit_id, "generated_at": generated_at, "persona": persona,
        "credit_score": score, "recommended_loan_zmw": loan, "new_evidence": [e["label"] for e in new_items],
    })

    return {
        "report_type": "Absa SME CreditGenie - Credit Assessment",
        "audit_id": audit_id,
        "case_id": case["case_id"],
        "assessment_number": len(case["history"]),
        "generated_at": generated_at,
        "persona": persona,
        "model": MODEL_NAME,
        "application": {k: app[k] for k in ("ref", "business_name", "requested_zmw", "purpose", "submitted_at")},
        "evidence": {
            "new_this_run": [e["label"] for e in new_items],
            "all_in_case": list(case["processed"].values()),
            "new_evidence_sha256": [e["fp"] for e in new_items],
        },
        "consent": {"primary": st.session_state.connection, "linked_accounts": app["linked_accounts"]},
        "privacy": {
            "local_redactions": redactions,
            "names_masked": mask_names,
            "personal_messages_removed": sum(e.get("filtered_out", 0) for e in app["evidence"]),
            "raw_input_retained": False,
        },
        "result": {
            **totals,
            "credit_score": score,
            "recommended_loan_zmw": loan,
            "ai_proposed_credit_score": analysis.credit_score,
            "ai_proposed_loan_zmw": analysis.loan_recommendation_zmw,
        },
        "policy_guardrails_applied": notes,
        "agent_summary": analysis.risk_summary,
        "agent_trace": trace,
        "previous_assessments": previous,
        "transactions": ledger,
        "analyst_decision": None,
    }


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
    if res["credit_score"] >= 70:
        return "Approve", ":material/check_circle:", "green"
    return "Approve with conditions", ":material/info:", "orange"


def kpi_tiles(report):
    res = report["result"]
    score = res["credit_score"]
    band = credit_band(score)
    previous = report["previous_assessments"]

    score_note = band
    if previous:
        change = score - previous[-1]["credit_score"]
        score_note += f" · {change:+d} vs last" if change else " · unchanged vs last"
    loan_note = ("Capped by Absa policy" if res["recommended_loan_zmw"] < res["ai_proposed_loan_zmw"]
                 else "Within Absa policy")

    def money(value):
        return f'<span class="cg-cur">ZMW</span>{value:,.0f}'

    meter = (f'<div class="cg-meter"><div class="cg-meter-fill" style="width:{score}%;background:{BAND_COLORS[band]}"></div>'
             '<div class="cg-meter-tick" style="left:40%"></div><div class="cg-meter-tick" style="left:70%"></div></div>')
    tiles = [
        ("Credit score", f'{score}<span class="cg-unit">/100</span>', score_note, meter),
        ("Recommended loan", money(res["recommended_loan_zmw"]),
         f"{loan_note} · asked ZMW {report['application']['requested_zmw']:,.0f}", ""),
        ("Net cash flow", money(res["net_cash_flow"]), "Income minus expenses", ""),
        ("Outstanding debt", money(res["outstanding_debt"]), "Owed, not yet paid", ""),
        ("Verified income", f'{res["verified_income_pct"]}<span class="cg-unit">%</span>',
         "Backed by MTN / Airtel records", ""),
    ]
    st.html('<div class="cg-kpis">' + "".join(
        f'<div class="cg-kpi"><div class="cg-kpi-label">{label}</div><div class="cg-kpi-value">{value}</div>'
        f'{extra}<div class="cg-kpi-note">{note}</div></div>'
        for label, value, note, extra in tiles
    ) + "</div>")


def record_decision(report, app):
    # Widget keys are per assessment, so a re-assessment starts from fresh, pre-filled values
    suffix = report["audit_id"]
    outcome = st.session_state[f"decision_outcome_{suffix}"]
    note = st.session_state[f"decision_note_{suffix}"].strip()
    amount = float(st.session_state[f"decision_amount_{suffix}"]) if outcome == "Approve" else 0.0
    recommended = report["result"]["recommended_loan_zmw"]
    override = (outcome == "Approve" and amount > recommended) or (outcome == "Decline" and recommended > 0)

    if outcome != "Approve" and not note:
        flash("error", "Add a note explaining the decision - the customer will see it.")
        return
    if override and not note:
        flash("error", "This overrides the AI and policy recommendation. Add a justification note.")
        return

    decision = {
        "outcome": outcome,
        "amount_zmw": amount,
        "note": note,
        "override_of_recommendation": override,
        "decided_at": now_iso(),
        "analyst": "Demo Analyst (simulated login)",
        "based_on_audit_id": report["audit_id"],
    }
    report["analyst_decision"] = decision
    app["decision"] = decision
    app["status"] = {"Approve": "Approved", "Decline": "Declined"}.get(outcome, "More info requested")
    app["updated_at"] = decision["decided_at"]
    st.session_state.toast = f"Decision recorded: {app['status']}. The SME Portal now shows it."


def analyst_dashboard():
    hero("Absa credit analyst", "CreditGenie Analyst Workbench",
         "The AI agent prepares the credit assessment from the SME's shared data. You make the final decision.",
         ["Multimodal GenAI", "PII masked before AI", "Policy-guarded", "Human decision & audit trail"],
         analyst=True)

    app = application
    if app is None:
        with st.container(border=True):
            st.markdown("#### :material/inbox: No applications waiting")
            st.caption("Switch to **SME Portal** in the sidebar, verify a business, share data and submit an "
                       "application. It will appear here.")
        return

    new_items = [e for e in app["evidence"] if e["fp"] not in case["processed"]]

    # --- APPLICATION ---
    with st.container(border=True):
        head_col, badge_col = st.columns([3, 1], vertical_alignment="center")
        head_col.markdown(f"#### {html.escape(app['business_name'])}")
        head_col.caption(f"Application {app['ref']} · ZMW {app['requested_zmw']:,.0f} for {app['purpose'].lower()} · "
                         f"submitted {pretty_time(app['submitted_at'])}")
        if app["linked_accounts"]:
            head_col.caption("Linked accounts: " + " · ".join(
                f"{link['provider']} {link['phone_masked']}" for link in app["linked_accounts"].values()))
        with badge_col.container(horizontal=True, horizontal_alignment="right"):
            status_badge(app["status"])
        with st.container(horizontal=True):
            for e in app["evidence"]:
                if e["fp"] in case["processed"]:
                    st.badge(f"Analysed · {e['label']}", icon=":material/check:", color="gray")
                else:
                    st.badge(f"New · {e['label']}", icon=":material/fiber_new:", color="blue")
        if new_items and case["history"]:
            st.info(f"The business sent {len(new_items)} new document(s) since the last assessment.",
                    icon=":material/notifications:")

    # --- RUN ---
    with st.container(border=True):
        st.markdown("**AI agent persona**")
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
                st.session_state.report = run_analysis(
                    persona, case, app, new_items, log=lambda m: status.write(f":material/check: {m}")
                )
                if app["status"] in ("Submitted", "More info requested"):
                    app["status"] = "Under review"
                status.update(label="Assessment complete", state="complete", expanded=False)
                st.session_state.toast = f"Assessment {st.session_state.report['assessment_number']} complete"
                st.rerun()  # redraw so the new evidence shows as "Analysed"
            except ValidationError as e:
                status.update(label="AI output rejected", state="error")
                st.error("The AI returned data that failed schema validation, so it was rejected.")
                st.code(str(e))
                st.write("**Raw AI Response (for debugging):**")
                st.code(st.session_state.get("raw_response", "No response received from the AI."))
            except Exception as e:
                status.update(label="Assessment failed", state="error")
                st.error(f"An error occurred: {e}")

    report = st.session_state.report
    if not report:
        return
    res = report["result"]

    # --- AI ASSESSMENT ---
    with st.container(border=True):
        title_col, badge_col = st.columns([3, 2], vertical_alignment="center")
        title_col.markdown("### AI credit assessment")
        title_col.caption(f"{report['persona']} · {pretty_time(report['generated_at'])} · "
                          f"Assessment {report['assessment_number']} of case {report['case_id']} · "
                          f"Audit ID `{report['audit_id']}`")
        label, icon, color = ai_recommendation(res)
        with badge_col.container(horizontal=True, horizontal_alignment="right"):
            st.badge(f"AI recommends: {label}", icon=icon, color=color)
            st.badge(credit_band(res["credit_score"]), color="gray")

        kpi_tiles(report)

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

            with summary_col:
                st.markdown("**Agent's summary**")
                st.write(report["agent_summary"])
                if report["policy_guardrails_applied"]:
                    st.markdown("**Policy guardrails applied**")
                    for note in report["policy_guardrails_applied"]:
                        st.warning(note, icon=":material/gavel:")
                else:
                    st.success("AI recommendation is within Absa credit policy.", icon=":material/gavel:")

        with tx_tab:
            st.caption("Synthetic financial statement for the whole case. Duplicates across sources are merged; "
                       "mobile-money records count as verified.")
            st.dataframe(
                pd.DataFrame(report["transactions"]),
                hide_index=True,
                width="stretch",
                column_config={
                    "date": "Date",
                    "description": st.column_config.TextColumn("Description", width="large"),
                    "type": "Type",
                    "amount_zmw": st.column_config.NumberColumn("Amount (ZMW)", format="%,.2f"),
                    "source": "Source",
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
            trace_col, json_col = st.columns(2, gap="large")
            with trace_col:
                st.markdown("**Compliance checks**")
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
                    (True, "Policy guardrails", f"{len(report['policy_guardrails_applied'])} applied by the rules engine"),
                    (report["analyst_decision"] is not None, "Human decision",
                     "Recorded by analyst" if report["analyst_decision"] else "Awaiting your decision"),
                ]
                for passed, title, detail in checks:
                    mark = ":green[:material/check_circle:]" if passed else ":gray[:material/radio_button_unchecked:]"
                    st.markdown(f"{mark} **{title}** · {detail}")
                st.markdown("**Agent steps**")
                for i, line in enumerate(report["agent_trace"], 1):
                    st.markdown(f"{i}. {line}")
                st.caption("Raw messages and images are not stored - the report keeps only SHA-256 fingerprints.")
            with json_col:
                st.markdown("**Report data**")
                st.json(report, expanded=False)
                st.download_button(
                    "Download Audit Report (JSON)",
                    data=json.dumps(report, indent=2),
                    file_name=f"creditgenie_{report['audit_id']}.json",
                    mime="application/json",
                    type="primary",
                    icon=":material/download:",
                    on_click="ignore",
                )

    # --- ANALYST DECISION (human in the loop) ---
    with st.container(border=True):
        st.markdown("### Your decision")
        decision = report["analyst_decision"]
        if decision:
            st.success(f"**{app['status']}**" + (f" · ZMW {decision['amount_zmw']:,.0f}" if decision["amount_zmw"] else "")
                       + f" · recorded {pretty_time(decision['decided_at'])}"
                       + (f"  \nNote: {decision['note']}" if decision["note"] else ""),
                       icon=":material/how_to_reg:")
            if decision["override_of_recommendation"]:
                st.caption("Flagged in the audit report as an override of the AI/policy recommendation.")
            st.caption("Run a new assessment to make another decision.")
        else:
            st.caption("The AI recommends; you decide. Your decision is sent to the SME Portal and stored in the audit report.")
            default_outcome = "Decline" if res["recommended_loan_zmw"] <= 0 else "Approve"
            suffix = report["audit_id"]
            outcome = st.segmented_control("Outcome", ("Approve", "Request more info", "Decline"),
                                           default=default_outcome, required=True,
                                           key=f"decision_outcome_{suffix}")
            if outcome == "Approve":
                st.number_input("Loan amount to offer (ZMW)", min_value=0.0,
                                value=float(res["recommended_loan_zmw"]), step=500.0,
                                key=f"decision_amount_{suffix}",
                                help="Pre-filled with the policy-checked AI recommendation.")
            placeholder = {"Approve": "Optional - e.g. repay over 6 months",
                           "Request more info": "e.g. Please upload last month's supplier receipts",
                           "Decline": "Explain the reason - the customer will see this"}[outcome]
            st.text_area("Note to the customer", key=f"decision_note_{suffix}", placeholder=placeholder, height=80)
            st.button("Record decision", type="primary", icon=":material/gavel:",
                      on_click=record_decision, args=(report, app))
            if flash_msg and flash_msg[0] == "error":
                st.error(flash_msg[1])


if role == "SME Portal":
    sme_portal()
else:
    analyst_dashboard()


# --- SIDEBAR (rendered last so it always reflects the latest state) ---
def side_rows(rows):
    return "".join(f'<div class="cg-row"><span>{k}</span><span>{v}</span></div>' for k, v in rows)


with st.sidebar:
    application = st.session_state.application
    report = st.session_state.report

    if role == "SME Portal":
        if application:
            st.html('<div class="cg-side-card"><div class="cg-side-label">Your application</div>' + side_rows([
                ("Reference", application["ref"]),
                ("Amount", f"ZMW {application['requested_zmw']:,.0f}"),
                ("Status", application["status"]),
                ("Documents sent", str(len(application["evidence"]))),
            ]) + "</div>")
            st.button("Start new application", on_click=start_new_case, icon=":material/restart_alt:",
                      width="stretch")
        else:
            st.html('<div class="cg-side-card"><div class="cg-side-label">How it works</div>' + side_rows([
                ("1", "Verify your number"),
                ("2", "Share your records"),
                ("3", "Apply - we reply here"),
            ]) + "</div>")
    else:
        applicant = html.escape(application["business_name"]) if application else "None"
        st.html('<div class="cg-side-card"><div class="cg-side-label">Current case</div>' + side_rows([
            ("Case", case["case_id"]),
            ("Applicant", applicant),
            ("Status", application["status"] if application else "-"),
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
        st.button("Start new case", on_click=start_new_case, icon=":material/restart_alt:", width="stretch",
                  help="Clears the application, evidence and decisions for the next customer.")

    st.html('<div class="cg-side-foot">Aligned with Zambia\'s Data Protection Act No. 3 of 2021 and POPIA. '
            f'Session started {pretty_time(st.session_state.session_started)}. '
            'Role switch simulates separate logins for the demo.</div>')
