from creditgenie.fraud import fraud_signals


def application(ref, business, phone="p1", evidence=("doc-a",), transactions=None):
    return {
        "ref": ref,
        "business_name": business,
        "consent": {"phone_sha256": phone},
        "linked_accounts": {},
        "evidence": [{"fp": fp, "label": f"Receipt: {fp}.png"} for fp in evidence],
        "report": {"transactions": transactions} if transactions is not None else None,
    }


def income(date, amount):
    return {"date": date, "type": "Income", "amount_zmw": amount, "source": "MTN MoMo"}


def test_no_signals_for_a_clean_application():
    apps = {"A": application("A", "Shop A", "p1", ["a"]), "B": application("B", "Shop B", "p2", ["b"])}
    assert fraud_signals(apps["A"], apps) == []


def test_same_document_from_another_business_is_flagged():
    apps = {"A": application("A", "Shop A", "p1", ["shared"]), "B": application("B", "Shop B", "p2", ["shared"])}
    signals = fraud_signals(apps["A"], apps)
    assert len(signals) == 1 and "Shop B" in signals[0]


def test_same_business_reapplying_is_not_flagged():
    apps = {"A": application("A", "Shop A", "p1", ["shared"]), "B": application("B", "shop a ", "p1", ["shared"])}
    assert fraud_signals(apps["A"], apps) == []


def test_one_phone_behind_two_businesses_is_flagged():
    apps = {"A": application("A", "Shop A", "same", ["a"]), "B": application("B", "Shop B", "same", ["b"])}
    assert any("phone number" in s for s in fraud_signals(apps["A"], apps))


def test_income_burst_before_applying_is_flagged():
    steady = [income(f"2026-{m:02d}-05", 1000) for m in range(5, 10)]
    burst = steady + [income("2026-10-20", 4000), income("2026-10-22", 4000)]
    apps = {"A": application("A", "Shop A", transactions=burst)}
    assert any("last 14 days" in s for s in fraud_signals(apps["A"], apps))
    apps = {"A": application("A", "Shop A", transactions=steady)}
    assert fraud_signals(apps["A"], apps) == []
