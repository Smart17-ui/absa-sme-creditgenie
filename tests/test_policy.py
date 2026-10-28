from types import SimpleNamespace

import pytest

from creditgenie.policy import (MAX_REPAYMENT_SHARE, apply_policy, compute_totals, cross_check, max_affordable_loan,
                                monthly_instalment, repayment_plan, scorecard, trading_history)
from creditgenie.schemas import Transaction


def tx(date, kind, amount, source="MTN MoMo", document=""):
    return Transaction(date=date, description="x", type=kind, amount_zmw=amount, source=source, document=document)


def six_month_business():
    """Wallet sales every month from April to October 2026, some expenses, a small debt."""
    sales = [tx(f"2026-{m:02d}-10", "Income", 3000) for m in range(4, 11)]
    costs = [tx(f"2026-{m:02d}-15", "Expense", 1000) for m in range(4, 11)]
    return sales + costs + [tx("2026-10-20", "Debt", 500, source="WhatsApp")]


def ai(score=50, loan=5000):
    return SimpleNamespace(credit_score=score, loan_recommendation_zmw=loan)


def test_scorecard_is_deterministic_and_bounded():
    txs = six_month_business()
    totals, history = compute_totals(txs), trading_history(txs)
    first = scorecard(totals, history)
    assert first == scorecard(totals, history)
    score, factors = first
    assert 1 <= score <= 100
    assert score == sum(f["points"] for f in factors)
    assert all(0 <= f["points"] <= f["max_points"] for f in factors)


def test_scorecard_rewards_verified_history():
    strong = six_month_business()
    weak = [tx("2026-10-10", "Income", 3000, source="Pasted text")]
    strong_score, _ = scorecard(compute_totals(strong), trading_history(strong))
    weak_score, _ = scorecard(compute_totals(weak), trading_history(weak))
    assert strong_score > weak_score


def test_ai_score_does_not_change_the_bank_score():
    txs = six_month_business()
    totals, history = compute_totals(txs), trading_history(txs)
    low = apply_policy("Financial Growth Coach", ai(score=5), totals, history, 10_000, 6)
    high = apply_policy("Financial Growth Coach", ai(score=99), totals, history, 10_000, 6)
    assert low[0] == high[0]
    assert low[2] == high[2]


def test_instalment_repays_principal_with_interest():
    plan = repayment_plan(10_000, 12)
    assert plan["monthly_instalment"] * 12 == pytest.approx(plan["total_repayable"], abs=0.1)
    assert plan["total_repayable"] > 10_000
    assert monthly_instalment(0, 6) == 0


def test_affordable_loan_keeps_repayments_within_limit():
    loan = max_affordable_loan(2000, 6)
    assert monthly_instalment(loan, 6) == pytest.approx(MAX_REPAYMENT_SHARE * 2000)
    assert max_affordable_loan(-100, 6) == 0
    assert max_affordable_loan(2000, 12) > max_affordable_loan(2000, 6)


def test_loan_never_exceeds_request_or_affordability():
    txs = six_month_business()
    totals, history = compute_totals(txs), trading_history(txs)
    _, _, loan, afford, _, reasons = apply_policy("Strict Risk Manager", ai(), totals, history, 1_000_000, 6)
    assert not reasons
    assert loan == pytest.approx(afford["max_affordable_loan"], abs=0.01)
    _, _, small, _, _, _ = apply_policy("Strict Risk Manager", ai(), totals, history, 1_000, 6)
    assert small == 1_000


def test_no_wallet_records_blocks_the_loan():
    txs = [tx("2026-10-10", "Income", 50_000, source="Image")]
    _, _, loan, _, _, reasons = apply_policy("Financial Growth Coach", ai(loan=40_000),
                                             compute_totals(txs), trading_history(txs), 10_000, 12)
    assert loan == 0
    assert [r["rule"] for r in reasons] == ["Verified wallet"]


def test_short_history_blocks_the_loan():
    txs = [tx("2026-09-01", "Income", 3000), tx("2026-10-01", "Income", 3000)]
    _, _, loan, _, _, reasons = apply_policy("Financial Growth Coach", ai(),
                                             compute_totals(txs), trading_history(txs), 5_000, 6)
    assert loan == 0
    assert reasons[0]["rule"] == "6 months of history"


def test_receipt_matching_a_wallet_payment_is_not_counted_twice():
    ledger = [t.model_dump() for t in (tx("2026-10-20", "Income", 4000),
                                       tx("2026-10-21", "Income", 4000, source="Image", document="Receipt: a.png"),
                                       tx("2026-10-21", "Income", 900, source="Image", document="Receipt: b.png"))]
    labelled = cross_check(ledger, flagged_documents=set())
    assert [t["evidence"] for t in labelled] == ["Wallet record", "Confirms a wallet record", "Self-reported"]


def test_records_from_a_flagged_document_are_set_aside():
    ledger = [tx("2026-10-21", "Income", 900, source="Image", document="Receipt: fake.png").model_dump()]
    assert cross_check(ledger, {"Receipt: fake.png"})[0]["evidence"] == "Flagged document - not counted"
