"""Applications and their case files, stored on local disk.

The application is what the SME submits; its case is the agent's memory of everything it has already
analysed for that application. Every application is kept, so the SME can see their past applications
and the analyst works through a queue.
"""
import datetime
import json
import os
import uuid

import streamlit as st
from cryptography.fernet import Fernet, InvalidToken

from creditgenie.config import DATA_DIR, DECIDED, RETENTION_DAYS, data_key
from creditgenie.utils import now_iso

SECTORS = ("Retail / grocery", "Agriculture", "Food & catering", "Transport", "Salon & beauty",
           "Tailoring & clothing", "Construction & hardware", "Services", "Other")
PROVINCES = ("Lusaka", "Copperbelt", "Central", "Eastern", "Northern", "Luapula", "Muchinga",
             "North-Western", "Southern", "Western")
OWNER_ROLES = ("Owner", "Director", "Partner")
# Widget key -> label. The profile is shown to the analyst; only the non-personal business fields
# (marked True) are given to the AI, so the owner's name, email and PACRA number never reach it.
PROFILE_FIELDS = {
    "owner_name": ("Owner's full name", False),
    "owner_role": ("Position", False),
    "owner_email": ("Email", False),
    "sector": ("Sector", True),
    "town": ("Town / district", True),
    "province": ("Province", True),
    "years_trading": ("Years trading", True),
    "employees": ("Employees", True),
    "pacra_number": ("PACRA registration", False),
    "description": ("What the business does", True),
}
REQUIRED_PROFILE = {"owner_name", "town", "description"}


def empty_case():
    return {
        "case_id": f"CASE-{uuid.uuid4().hex[:6].upper()}",
        "opened_at": now_iso(),
        "ledger": [],     # every transaction found so far (already redacted)
        "flags": [],      # documents the AI flagged for possible tampering
        "processed": {},  # input fingerprint -> label, so the same evidence is never counted twice
        "name_map": {},   # real name -> "Customer X"; stays in the local store, never in the audit report
        "history": [],    # one entry per assessment run
    }


def clear_portal_form():
    st.session_state.connection = None
    st.session_state.linked = {}
    st.session_state.synced = {}
    st.session_state.messy_data = ""
    st.session_state.uploader_key += 1  # clears the file uploader
    st.session_state.pop("otp", None)
    st.session_state.pop("link_otp", None)


def start_new_application():
    """Open a blank form. Earlier applications stay in the list for the SME and the analyst."""
    st.session_state.current_ref = None
    clear_portal_form()
    for field in PROFILE_FIELDS:
        st.session_state.pop(f"profile_{field}", None)


def open_application(ref):
    st.session_state.current_ref = ref
    clear_portal_form()
    # The business was verified when it applied, so it can send more documents straight away
    st.session_state.connection = st.session_state.applications[ref]["consent"]


# --- STORE (local disk, so applications survive restarts and are shared between browser tabs) ---
# Everything on disk is encrypted with the key in .env. Raw receipts and messages are kept only until the
# AI has analysed them; after that an application keeps just each document's label and SHA-256 fingerprint.
STORE_PATH = os.path.join(DATA_DIR, "applications.json")
EVIDENCE_DIR = os.path.join(DATA_DIR, "evidence")
VIEWERS = ("sme", "analyst")  # each side has its own trash; deleting on one side never touches the other


def _decrypt(blob, legacy_ok):
    try:
        return Fernet(data_key()).decrypt(blob)
    except InvalidToken:
        if legacy_ok:
            return blob  # written before encryption was switched on; encrypted at the next save
        raise RuntimeError("data/applications.json can't be decrypted - CREDITGENIE_DATA_KEY in .env doesn't "
                           "match the key it was saved with.") from None


def save_applications():
    apps = st.session_state.applications
    fernet = Fernet(data_key())
    records, kept_files = {}, set()
    os.makedirs(EVIDENCE_DIR, exist_ok=True)
    for ref, a in apps.items():
        evidence = []
        for e in a["evidence"]:
            if e["fp"] in a["case"]["processed"]:
                e.pop("text", None)
                e.pop("image", None)
            stored = {k: v for k, v in e.items() if k != "image"}
            if "image" in e:
                name = e["fp"] + ".enc"
                path = os.path.join(EVIDENCE_DIR, name)
                if not os.path.exists(path):
                    with open(path, "wb") as f:
                        f.write(fernet.encrypt(e["image"]))
                stored["image_file"] = name
                kept_files.add(name)
            evidence.append(stored)
        records[ref] = {**a, "evidence": evidence}
    tmp = STORE_PATH + ".tmp"
    with open(tmp, "wb") as f:
        f.write(fernet.encrypt(json.dumps(records).encode()))
    os.replace(tmp, STORE_PATH)  # atomic, so another tab never reads a half-written file
    for name in set(os.listdir(EVIDENCE_DIR)) - kept_files:
        os.remove(os.path.join(EVIDENCE_DIR, name))
    st.session_state.store_mtime = os.path.getmtime(STORE_PATH)


def load_applications():
    if not os.path.exists(STORE_PATH):
        return {}
    with open(STORE_PATH, "rb") as f:
        blob = f.read()
    records = json.loads(_decrypt(blob, legacy_ok=blob.lstrip().startswith(b"{")))
    for a in records.values():
        for e in a["evidence"]:
            name = e.pop("image_file", None)
            if name and os.path.exists(os.path.join(EVIDENCE_DIR, name)):
                with open(os.path.join(EVIDENCE_DIR, name), "rb") as img:
                    e["image"] = _decrypt(img.read(), legacy_ok=not name.endswith(".enc"))
    return records


def purge_expired(apps):
    """Data retention: erase decided applications RETENTION_DAYS after their last update."""
    cutoff = datetime.datetime.now().astimezone() - datetime.timedelta(days=RETENTION_DAYS)
    expired = [ref for ref, a in apps.items()
               if a["status"] in DECIDED and datetime.datetime.fromisoformat(a["updated_at"]) < cutoff]
    for ref in expired:
        del apps[ref]
    return bool(expired)


def trash_application(ref, viewer):
    st.session_state.applications[ref]["trashed"][viewer] = now_iso()
    if viewer == "sme" and st.session_state.current_ref == ref:
        start_new_application()
    if viewer == "analyst" and st.session_state.get("analyst_ref") == ref:
        st.session_state.analyst_ref = None
    save_applications()
    st.session_state.toast = f"{ref} moved to trash"


def restore_application(ref, viewer):
    st.session_state.applications[ref]["trashed"][viewer] = None
    save_applications()
    st.session_state.toast = f"{ref} restored"


def delete_application(ref, viewer):
    apps = st.session_state.applications
    apps[ref]["deleted"][viewer] = True
    if all(apps[ref]["deleted"].values()):
        del apps[ref]  # nobody can see it any more, so remove it and its documents for good
    save_applications()
    st.session_state.toast = f"{ref} deleted permanently"


def init_session():
    """Set up session state (survives Streamlit reruns) and load the latest applications from disk."""
    for key, default in {"messy_data": "", "synced": {}, "linked": {}, "connection": None, "uploader_key": 0,
                         "current_ref": None, "session_started": now_iso()}.items():
        st.session_state.setdefault(key, default)
    store_mtime = os.path.getmtime(STORE_PATH) if os.path.exists(STORE_PATH) else None
    if "applications" not in st.session_state or st.session_state.get("store_mtime") != store_mtime:
        st.session_state.applications = load_applications()  # first run, or another tab saved a change
        st.session_state.store_mtime = store_mtime
        if purge_expired(st.session_state.applications):
            save_applications()
    for a in st.session_state.applications.values():
        a.setdefault("trashed", dict.fromkeys(VIEWERS))  # viewer -> when they moved it to trash
        a.setdefault("deleted", dict.fromkeys(VIEWERS, False))
        a.setdefault("term_months", 6)
        a.setdefault("decisions", [])        # every decision and second approval, oldest first; never edited
        a.setdefault("offer_response", None)  # the customer accepting or declining an approved offer
    if st.session_state.current_ref not in st.session_state.applications:
        st.session_state.current_ref = None


def sme_application():
    """The application open in the SME Portal, or None while filling in a new one."""
    return st.session_state.applications.get(st.session_state.current_ref)
