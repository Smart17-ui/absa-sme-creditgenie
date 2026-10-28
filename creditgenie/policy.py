"""Deterministic credit policy - the AI extracts the records, the bank's rules score them and size the loan."""
import datetime

from creditgenie.feeds import VERIFIED_SOURCES

MIN_HISTORY_DAYS = 180   # about 6 months between the oldest and newest dated record
MIN_ACTIVE_MONTHS = 4    # sales in at least this many different calendar months
DAYS_PER_MONTH = 30.4

# Loan pricing and affordability. The rate is illustrative for the prototype, not an Absa price.
LOAN_TERMS = (3, 6, 12)  # months
ANNUAL_INTEREST_RATE = 0.30
MAX_REPAYMENT_SHARE = 0.40  # repayments may use at most 40% of verified monthly free cash flow
MIN_LOAN_ZMW = 500
# Four-eyes rule: a second analyst must confirm large loans and any override of the recommendation
FOUR_EYES_LIMIT_ZMW = 25_000


def parse_date(text):
    """A transaction date the bank can rely on, or None. Future dates don't count."""
    for fmt in ("%Y-%m-%d", "%d/%m/%Y"):
        try:
            d = datetime.datetime.strptime(text.strip(), fmt).date()
        except (ValueError, AttributeError):
            continue
        return d if d <= datetime.date.today() else None
    return None


def trading_history(transactions):
    """How long, and how regularly, the records show the business trading.
    Only dated wallet records (MTN MoMo / Airtel Money) count: typed, pasted or photographed records
    can be faked, so they can't prove how long a business has traded."""
    undated = sum(1 for t in transactions if parse_date(t.date) is None)
    dated = [(d, t) for t in transactions if t.source in VERIFIED_SOURCES and (d := parse_date(t.date))]
    if not dated:
        return {"dated_transactions": 0, "undated_transactions": undated, "first": None,
                "last": None, "span_days": 0, "months": 0.0, "active_months": 0}
    dates = [d for d, _ in dated]
    span = (max(dates) - min(dates)).days
    return {
        "dated_transactions": len(dated),
        "undated_transactions": undated,
        "first": min(dates).isoformat(),
        "last": max(dates).isoformat(),
        "span_days": span,
        "months": round(span / DAYS_PER_MONTH, 1),
        "active_months": len({(d.year, d.month) for d, t in dated if t.type == "Income"}),
    }


NOT_COUNTED = ("Confirms a wallet record", "Flagged document - not counted")


def cross_check(ledger, flagged_documents):
    """Label every record by how far it can be trusted. A self-reported record (receipt, chat, pasted
    text) that matches a wallet payment - same type, amount within 1%, date within 3 days - confirms
    that payment, and is not counted a second time. Records from a document flagged for possible
    tampering are set aside until an analyst has looked at it."""
    wallet = [(parse_date(w["date"]), w) for w in ledger if w["source"] in VERIFIED_SOURCES]
    for t in ledger:
        if t["source"] in VERIFIED_SOURCES:
            t["evidence"] = "Wallet record"
            continue
        if t["document"].strip() and t["document"].strip() in flagged_documents:
            t["evidence"] = "Flagged document - not counted"
            continue
        d = parse_date(t["date"])
        confirms = d is not None and any(
            wd is not None and w["type"] == t["type"] and abs((wd - d).days) <= 3
            and abs(w["amount_zmw"] - t["amount_zmw"]) <= max(1.0, 0.01 * w["amount_zmw"])
            for wd, w in wallet
        )
        t["evidence"] = "Confirms a wallet record" if confirms else "Self-reported"
    return ledger


def scorecard(totals, history):
    """Absa's credit score: a fixed formula over the records, so the same case always gets the same
    score and every point can be explained. Returns (score 1-100, one row per factor)."""
    income = totals["total_income"]
    margin = totals["net_cash_flow"] / income if income else 0.0
    debt_ratio = totals["outstanding_debt"] / income if income else 1.0
    factors = [
        ("Verified income", 25, totals["verified_income_pct"] / 100,
         f"{totals['verified_income_pct']}% of income is in MTN / Airtel wallet records"),
        ("Trading history", 20, history["span_days"] / MIN_HISTORY_DAYS,
         f"{history['months']} months of dated wallet records (full points at 6)"),
        ("Regular sales", 20, history["active_months"] / 6,
         f"Sales in {history['active_months']} different month(s) (full points at 6)"),
        ("Profit margin", 20, margin / 0.30,
         f"Keeps {margin:.0%} of income after expenses (full points at 30%)" if income else "No income recorded"),
        ("Debt burden", 15, 1 - debt_ratio / 0.5,
         f"Debt owed is {debt_ratio:.0%} of income (no debt earns full points, 50% or more earns none)"
         if income else "No income recorded"),
    ]
    rows = [{"factor": name, "points": round(most * min(1.0, max(0.0, share))), "max_points": most, "detail": detail}
            for name, most, share, detail in factors]
    return max(1, sum(r["points"] for r in rows)), rows


def monthly_instalment(principal, term_months, annual_rate=ANNUAL_INTEREST_RATE):
    """Equal monthly repayment that clears the loan and its interest over the term."""
    rate = annual_rate / 12
    if principal <= 0:
        return 0.0
    return principal * rate / (1 - (1 + rate) ** -term_months) if rate else principal / term_months


def max_affordable_loan(monthly_free_cash, term_months, annual_rate=ANNUAL_INTEREST_RATE):
    """The largest loan whose repayments stay within MAX_REPAYMENT_SHARE of monthly free cash flow."""
    budget = MAX_REPAYMENT_SHARE * monthly_free_cash
    if budget <= 0:
        return 0.0
    rate = annual_rate / 12
    return budget * (1 - (1 + rate) ** -term_months) / rate if rate else budget * term_months


def repayment_plan(principal, term_months, annual_rate=ANNUAL_INTEREST_RATE):
    instalment = monthly_instalment(principal, term_months, annual_rate)
    return {"amount_zmw": round(principal, 2), "term_months": term_months, "annual_rate": annual_rate,
            "monthly_instalment": round(instalment, 2), "total_repayable": round(instalment * term_months, 2)}


def apply_policy(persona, analysis, totals, history, requested_zmw, term_months):
    """The bank's rules decide the score and the loan; the AI's own figures are kept as a second opinion.
    Returns (score, score factors, loan, affordability, notes for the analyst, reasons the customer can act on)."""
    score, factors = scorecard(totals, history)
    notes = []
    reasons = []
    free_cash = totals["net_cash_flow"] - totals["outstanding_debt"]
    since = (datetime.date.today() - datetime.timedelta(days=MIN_HISTORY_DAYS)).strftime("%B %Y")

    # Fraud guard: typed, pasted and photographed records can be faked, so a loan needs wallet records
    verified_free_cash = totals["verified_income"] - totals["total_expense"] - totals["outstanding_debt"]
    if totals["verified_income"] <= 0:
        reasons.append({
            "rule": "Verified wallet",
            "reason": "Your application only has records you typed, pasted or photographed. We can't check "
                      "these, so Absa can't lend on them alone.",
            "fix": "Connect your MTN MoMo or Airtel Money wallet so we can confirm your sales.",
        })
    elif history["dated_transactions"] < 2:
        reasons.append({
            "rule": "Dated records",
            "reason": "Too few of your wallet records show a date, so we can't see how long your business "
                      "has been trading.",
            "fix": "Connect the wallet you use most for your business.",
        })
    elif history["span_days"] < MIN_HISTORY_DAYS:
        first = datetime.date.fromisoformat(history["first"]).strftime("%d %b %Y")
        last = datetime.date.fromisoformat(history["last"]).strftime("%d %b %Y")
        reasons.append({
            "rule": "6 months of history",
            "reason": f"Your wallet records cover {history['span_days']} days ({first} to {last}). "
                      "Absa needs at least 6 months of trading history.",
            "fix": f"Connect a wallet you have used for your business since {since}.",
        })
    elif history["active_months"] < MIN_ACTIVE_MONTHS:
        reasons.append({
            "rule": "Regular sales",
            "reason": f"Your wallet records show sales in only {history['active_months']} month(s). "
                      f"Absa looks for sales in at least {MIN_ACTIVE_MONTHS} different months.",
            "fix": "Connect any other wallet you receive customer payments on.",
        })
    if free_cash <= 0:
        reasons.append({
            "rule": "Positive cash flow",
            "reason": "Your recorded expenses and debts are equal to or higher than your income.",
            "fix": "Share any income records that are missing, or apply again once debts are paid down.",
        })
    elif totals["verified_income"] > 0 and verified_free_cash <= 0:
        reasons.append({
            "rule": "Verified cash flow",
            "reason": "The income in your connected wallets doesn't cover your expenses and debts. "
                      "Income we can't check isn't enough to lend on.",
            "fix": "Connect any other wallet you receive customer payments on.",
        })

    # Size the loan on verified money only: wallet income minus ALL expenses and debts, so income
    # nobody can check (typed, pasted, photographed) never makes the loan bigger
    verified_monthly_free_cash = max(0.0, verified_free_cash) / max(1.0, history["months"])
    affordable = max_affordable_loan(verified_monthly_free_cash, term_months)
    affordability = {
        "verified_monthly_free_cash": round(verified_monthly_free_cash, 2),
        "max_monthly_instalment": round(MAX_REPAYMENT_SHARE * verified_monthly_free_cash, 2),
        "max_affordable_loan": round(affordable, 2),
        "term_months": term_months,
        "annual_rate": ANNUAL_INTEREST_RATE,
    }
    if not reasons and affordable < MIN_LOAN_ZMW:
        reasons.append({
            "rule": "Affordability",
            "reason": f"Your verified cash flow can't cover repayments on the smallest loan "
                      f"(ZMW {MIN_LOAN_ZMW:,}) over {term_months} months.",
            "fix": "Apply over a longer term, or connect any other wallet you receive customer payments on.",
        })

    loan = min(requested_zmw, affordable)
    if reasons:
        loan = 0.0
    elif affordable < requested_zmw:
        notes.append(f"Loan reduced from the ZMW {requested_zmw:,.0f} requested to ZMW {affordable:,.0f}: repayments "
                     f"over {term_months} months must stay under {MAX_REPAYMENT_SHARE:.0%} of verified monthly free "
                     f"cash flow (ZMW {verified_monthly_free_cash:,.0f}).")
    if persona == "Strict Risk Manager" and free_cash <= 0 and score >= 40:
        notes.append(f"Score capped at 39 (scorecard gave {score}): negative cash flow after debts.")
        score = 39
    if loan > 0 and analysis.loan_recommendation_zmw < 0.5 * loan:
        notes.append(f"The AI suggested a much smaller loan (ZMW {analysis.loan_recommendation_zmw:,.0f}). "
                     "Read its summary before deciding.")

    return score, factors, round(loan, 2), affordability, notes, reasons


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
        "verified_income": round(verified_income, 2),
        # Share of income backed by a mobile-money record rather than a chat message or claim
        "verified_income_pct": round(100 * verified_income / income) if income else 0,
    }
