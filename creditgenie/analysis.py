"""The AI credit agent: sends new evidence to Gemini, then re-scores the whole case with the bank's rules."""
import io
import json
import time
import uuid

import httpx
import PIL.Image
import PIL.ImageOps
import streamlit as st
from google import genai
from google.genai import errors as genai_errors

from creditgenie.applications import PROFILE_FIELDS
from creditgenie.config import MODEL_NAME, api_key
from creditgenie.policy import (NOT_COUNTED, apply_policy, compute_totals, cross_check, repayment_plan,
                                trading_history)
from creditgenie.privacy import redact
from creditgenie.prompts import OUTPUT_RULES, PERSONA_INSTRUCTIONS
from creditgenie.schemas import CreditAnalysis, Transaction
from creditgenie.utils import now_iso

MAX_IMAGE_SIDE = 1600  # pixels; plenty to read a receipt, and keeps several phone photos under Gemini's size limit
AI_TIMEOUT_MS = 90_000
AI_ATTEMPTS = 3


class AIBusyError(Exception):
    """Gemini stayed overloaded or unreachable after every retry."""


# The Gemini client is cached so it isn't rebuilt on every rerun (a new key in .env gets a new client)
@st.cache_resource
def get_client(key):
    return genai.Client(api_key=key, http_options={"timeout": AI_TIMEOUT_MS})


def prepare_image(data):
    """Upright and no bigger than MAX_IMAGE_SIDE, so large phone photos don't fail the request."""
    image = PIL.ImageOps.exif_transpose(PIL.Image.open(io.BytesIO(data)))
    image.thumbnail((MAX_IMAGE_SIDE, MAX_IMAGE_SIDE))
    return image


def generate_with_retry(contents, step):
    """Call Gemini, retrying when it is overloaded (429 / 5xx) or times out. A bad key is not retried."""
    for attempt in range(1, AI_ATTEMPTS + 1):
        try:
            return get_client(api_key()).models.generate_content(
                model=MODEL_NAME,
                contents=contents,
                config={
                    "response_mime_type": "application/json",
                    "response_schema": CreditAnalysis,
                    "temperature": 0.2,
                },
            )
        except (genai_errors.ServerError, genai_errors.ClientError, httpx.TransportError, TimeoutError) as e:
            retryable = not isinstance(e, genai_errors.ClientError) or e.code == 429
            if not retryable:
                raise
            if attempt == AI_ATTEMPTS:
                raise AIBusyError(str(e)) from e
            step(f"AI service busy ({getattr(e, 'code', None) or type(e).__name__}) - retrying "
                 f"(attempt {attempt + 1} of {AI_ATTEMPTS})")
            time.sleep(2 * attempt)


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
    business = {PROFILE_FIELDS[k][0]: v for k, v in app["profile"].items() if PROFILE_FIELDS[k][1]}
    business[PROFILE_FIELDS["description"][0]], redactions = redact(business[PROFILE_FIELDS["description"][0]],
                                                                    mask_names, case["name_map"])
    contents.append("Business profile (declared by the owner, not verified - use it for context only, "
                    "never as evidence of income):\n" + json.dumps(business, indent=1))

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
            contents.append(prepare_image(item["image"]))

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

    response = generate_with_retry(contents, step)
    st.session_state.raw_response = response.text
    analysis = CreditAnalysis.model_validate_json(response.text)
    step(f"{persona} extracted {len(analysis.synthetic_transactions)} new transaction(s) "
         f"with {MODEL_NAME}; output passed schema validation")

    # Second privacy pass on what came back (catches PII the AI read from an image)
    for t in analysis.synthetic_transactions:
        t.description, n = redact(t.description, mask_names, case["name_map"])
        redactions += n

    audit_id = f"CG-{uuid.uuid4().hex[:10].upper()}"
    flags = case["flags"] + [f.model_dump() for f in analysis.suspicious_documents]
    ledger = cross_check(case["ledger"] + [{**t.model_dump(), "added_by": audit_id}
                                           for t in analysis.synthetic_transactions],
                         {f["document"].strip() for f in flags})
    # A record that only confirms a wallet payment is evidence, not extra money; records from a
    # flagged document wait for the analyst instead of moving the numbers either way
    transactions = [Transaction(**t) for t in ledger if t["evidence"] not in NOT_COUNTED]
    totals = compute_totals(transactions)
    history = trading_history(transactions)
    confirmed = sum(t["evidence"] == "Confirms a wallet record" for t in ledger)
    unchecked = sum(t["evidence"] == "Self-reported" for t in ledger)
    set_aside = sum(t["evidence"] == "Flagged document - not counted" for t in ledger)
    step(f"Cross-check: {confirmed} self-reported record(s) confirm a wallet payment and were not counted twice; "
         f"{unchecked} can't be checked against a wallet")
    step(f"Document check: {len(analysis.suspicious_documents)} new document(s) flagged for possible tampering; "
         f"{set_aside} record(s) from flagged documents set aside until reviewed"
         if analysis.suspicious_documents else "Document check: no signs of tampering in new documents")
    step(f"Rules engine computed totals over {len(transactions)} transaction(s) in the case")
    step(f"Verified history: {history['span_days']} days of wallet records with sales in "
         f"{history['active_months']} month(s); {totals['verified_income_pct']}% of income is wallet-verified; "
         f"{history['undated_transactions']} undated record(s) not counted")
    term = app.get("term_months", 6)
    score, factors, loan, affordability, notes, reasons = apply_policy(
        persona, analysis, totals, history, app["requested_zmw"], term)
    step(f"Absa scorecard: {score}/100 (the AI's own estimate was {analysis.credit_score}/100)")
    step(f"Affordability: verified free cash flow of ZMW {affordability['verified_monthly_free_cash']:,.0f}/month "
         f"supports up to ZMW {affordability['max_affordable_loan']:,.0f} over {term} months")
    step(f"Absa credit policy applied: {len(notes)} guardrail(s) triggered"
         + (f"; loan blocked - {', '.join(r['rule'] for r in reasons)} not met" if reasons else ""))
    step(f"Audit report {audit_id} generated - awaiting analyst decision")

    generated_at = now_iso()
    previous = list(case["history"])
    case["ledger"] = ledger
    case["flags"] = flags
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
        "application": {**{k: app[k] for k in ("ref", "business_name", "requested_zmw", "purpose", "submitted_at",
                                               "profile")}, "term_months": term},
        "evidence": {
            "new_this_run": [e["label"] for e in new_items],
            "all_in_case": list(case["processed"].values()),
            "new_evidence_sha256": [e["fp"] for e in new_items],
        },
        "consent": {"primary": app["consent"], "linked_accounts": app["linked_accounts"]},
        "privacy": {
            "local_redactions": redactions,
            "names_masked": mask_names,
            "personal_messages_removed": sum(e.get("filtered_out", 0) for e in app["evidence"]),
            "raw_input_retained": False,
        },
        "result": {
            **totals,
            "credit_score": score,
            "score_factors": factors,
            "recommended_loan_zmw": loan,
            "repayment_plan": repayment_plan(loan, term) if loan > 0 else None,
            "affordability": affordability,
            "ai_proposed_credit_score": analysis.credit_score,
            "ai_proposed_loan_zmw": analysis.loan_recommendation_zmw,
            "trading_history": history,
            "decline_reasons": reasons,
            "suspicious_documents": flags,
            "self_reported_confirmed": confirmed,
            "self_reported_unchecked": unchecked,
        },
        "policy_guardrails_applied": notes,
        "agent_summary": analysis.risk_summary,
        "agent_trace": trace,
        "previous_assessments": previous,
        "transactions": ledger,
        "analyst_decision": None,
    }
