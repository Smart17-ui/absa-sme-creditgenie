"""Small helpers used across the app."""
import datetime
import hashlib

import streamlit as st


def now_iso():
    return datetime.datetime.now().astimezone().isoformat(timespec="seconds")


def pretty_time(iso):
    return datetime.datetime.fromisoformat(iso).strftime("%d %b %Y, %H:%M")


def fingerprint(data):
    return hashlib.sha256(data).hexdigest()


def flash(kind, message):
    """A one-off message shown on the next run (callbacks can't draw on the page themselves)."""
    st.session_state.flash = (kind, message)
