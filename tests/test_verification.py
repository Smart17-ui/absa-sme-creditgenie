import pytest

from creditgenie.verification import invalid_phone_message, normalise_phone


@pytest.mark.parametrize("raw", [
    "0974527123", "097 452 7123", "097-452-7123", "(097) 452 7123", "+260974527123", "260974527123",
    "974527123",                 # leading 0 left off
    "​0974527123",          # zero-width space copied from WhatsApp
    "097‑4527123",          # non-breaking hyphen
    "097 452 7123",    # non-breaking spaces
    "０９７４５２７１２３",  # full-width digits
])
def test_airtel_number_is_accepted_however_it_was_typed_or_pasted(raw):
    assert normalise_phone(raw) == "+260974527123"


@pytest.mark.parametrize("raw", ["097452712", "09745271234", "0914527123", "0874527123", "", "abc"])
def test_invalid_numbers_are_rejected(raw):
    assert normalise_phone(raw) is None


def test_error_message_says_what_was_read():
    assert "9 digits read" in invalid_phone_message("097452712")
