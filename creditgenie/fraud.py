"""Fraud signals that only show up when an application is compared with all the others.

These are warnings for the analyst, never automatic declines: a family running two shops can share a
phone, and the analyst is best placed to judge."""
from creditgenie.feeds import VERIFIED_SOURCES
from creditgenie.policy import parse_date

SPIKE_WINDOW_DAYS = 14
SPIKE_SHARE = 0.5      # half or more of all verified income arriving in the last two weeks...
SPIKE_MIN_SPAN_DAYS = 60  # ...of a history long enough for that to be unusual


def _same_business(a, b):
    return a["business_name"].strip().lower() == b["business_name"].strip().lower()


def _phones(app):
    return {app["consent"]["phone_sha256"]} | {link["phone_sha256"] for link in app["linked_accounts"].values()}


def fraud_signals(app, applications):
    """Plain-language warnings about `app`, checked against every other application on record
    (including ones in trash - deleting an application must not hide its history)."""
    signals = []
    others = [o for ref, o in applications.items() if ref != app["ref"] and not _same_business(app, o)]

    # 1. The same document sent by a different business: a receipt or statement can't belong to two firms
    for e in app["evidence"]:
        reused = [o for o in others if any(x["fp"] == e["fp"] for x in o["evidence"])]
        if reused:
            signals.append(f"\"{e['label']}\" was also sent by "
                           + ", ".join(f"{o['business_name']} ({o['ref']})" for o in reused)
                           + " - the same document can't belong to two businesses.")

    # 2. One verified phone number behind several businesses
    mine = _phones(app)
    shared = [o for o in others if mine & _phones(o)]
    if shared:
        signals.append("A verified phone number on this application is also used by "
                       + ", ".join(f"{o['business_name']} ({o['ref']})" for o in shared)
                       + ". Check the businesses are really separate.")

    # 3. A burst of wallet income just before applying: money cycled through the wallet to look like sales
    if app.get("report"):
        income = [(d, t["amount_zmw"]) for t in app["report"]["transactions"]
                  if t["source"] in VERIFIED_SOURCES and t["type"] == "Income" and (d := parse_date(t["date"]))]
        if income:
            first, last = min(d for d, _ in income), max(d for d, _ in income)
            total = sum(a for _, a in income)
            recent = sum(a for d, a in income if (last - d).days <= SPIKE_WINDOW_DAYS)
            if (last - first).days >= SPIKE_MIN_SPAN_DAYS and total and recent / total >= SPIKE_SHARE:
                signals.append(f"{recent / total:.0%} of all verified income arrived in the last "
                               f"{SPIKE_WINDOW_DAYS} days of the records. Check it is real trade, not money "
                               "moved through the wallet to look like sales.")
    return signals
