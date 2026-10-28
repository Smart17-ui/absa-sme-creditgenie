"""Absa SME CreditGenie - run with: streamlit run app.py

The code lives in the creditgenie package:
  config.py        settings from .env, paths and constants
  feeds.py         simulated MTN MoMo / Airtel Money / WhatsApp data, and what counts as verified
  verification.py  SMS OTP, consent and linking accounts
  applications.py  applications, case files, local storage and trash
  privacy.py       PII redaction before anything reaches the AI
  schemas.py       the JSON shape the AI must return
  prompts.py       AI persona instructions
  analysis.py      the AI credit agent
  policy.py        Absa's deterministic credit rules
  ui/              the SME Portal, Analyst Workbench, sidebar, shared components and styles
"""
import streamlit as st

from creditgenie.applications import init_session
from creditgenie.config import LOGO_PATH, api_key
from creditgenie.ui import sidebar
from creditgenie.ui.analyst import analyst_dashboard
from creditgenie.ui.sme_portal import sme_portal
from creditgenie.ui.styles import inject_styles

st.set_page_config(page_title="Absa SME CreditGenie", page_icon=LOGO_PATH, layout="wide")

if not api_key():
    st.error("API Key not found. Please check your .env file.")
    st.stop()

init_session()
inject_styles()

flash_msg = st.session_state.pop("flash", None)
if "toast" in st.session_state:
    st.toast(st.session_state.pop("toast"), icon=":material/task_alt:")

role = sidebar.render_role_switch()
if role == "SME Portal":
    sme_portal(flash_msg)
else:
    analyst_dashboard(flash_msg)
sidebar.render_summary(role)
