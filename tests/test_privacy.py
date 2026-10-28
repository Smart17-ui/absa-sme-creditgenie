from creditgenie.feeds import keep_money_messages
from creditgenie.privacy import redact


def test_phone_and_nrc_numbers_are_always_masked():
    text, count = redact("Paid by 0971234567 and +260 96 123 4567, NRC 123456/78/1", False, {})
    assert "0971234567" not in text and "123 4567" not in text and "123456/78/1" not in text
    assert count == 3


def test_names_are_masked_consistently_across_sources():
    name_map = {}
    chat, _ = redact("[20/10/2026, 08:55] Mama Chileshe: sent the 4,000", True, name_map)
    sms, _ = redact("received ZMW 4,000.00 from MAMA CHILESHE on 20/10/2026", True, name_map)
    assert "Chileshe" not in chat and "CHILESHE" not in sms
    assert "Customer A" in chat and "Customer A" in sms


def test_names_are_kept_when_masking_is_off():
    text, _ = redact("Received 1,200 from Bashi Mwansa", False, {})
    assert "Bashi Mwansa" in text


def test_place_names_are_not_mistaken_for_people():
    text, _ = redact("Paid 1,500 to Lusaka market for transport", True, {})
    assert "Lusaka" in text


def test_only_money_messages_are_shared():
    chat = ("[21/10/2026, 19:40] Mama Chileshe: Are you coming to the kitchen party on Saturday?\n"
            "[22/10/2026, 17:30] Supplier: Balance of K1,000 is still outstanding")
    kept, removed = keep_money_messages(chat)
    assert removed == 1
    assert "K1,000" in kept and "kitchen party" not in kept
