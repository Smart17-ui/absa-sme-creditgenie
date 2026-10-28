"""Local PII redaction - runs BEFORE anything is sent to the AI."""
import re


# Names may be "Mama Chileshe" (chat) or "MAMA CHILESHE" (mobile-money SMS), so allow all-caps words
NAME = r"[A-Z][A-Za-z]+(?:\s+[A-Z][A-Za-z]+){0,2}"
PHONE_RE = re.compile(r"(?:\+?260[\s-]?|\b0)[79]\d(?:[\s-]?\d){7}\b")
NRC_RE = re.compile(r"\b\d{6}/\d{2}/\d\b")
HONORIFIC_NAME_RE = re.compile(rf"\b(?i:Mama|Bashi|Ba|Mr|Mrs|Ms|Dr|Madam)\.?\s+{NAME}")
PARTY_NAME_RE = re.compile(rf"\b((?:from|to|by)\s+)({NAME})")
WHATSAPP_SENDER_RE = re.compile(rf"^(\[[^\]]+\]\s*)({NAME})(?=:)", re.MULTILINE)
NOT_A_NAME = {
    "Customer", "Supplier", "Vendor", "Landlord", "Transport", "Market", "Bank",
    "Absa", "Mobile", "Money", "Airtel", "Zamtel", "Zesco", "Shoprite", "Cash",
    "Mtn", "Momo", "Merchant", "Fuel", "You", "Me",
    "Lusaka", "Ndola", "Kitwe", "Livingstone", "Kabwe", "Chipata", "Solwezi",
    "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday",
    "January", "February", "March", "April", "May", "June", "July", "August",
    "September", "October", "November", "December",
}


def redact(text, mask_names, name_map):
    """Mask phone/NRC numbers always, and personal names when mask_names is True.
    name_map keeps labels consistent ("Mama Chileshe" -> "Customer A" everywhere)."""
    count = 0

    def label_for(name):
        name = name.title()  # "MAMA CHILESHE" and "Mama Chileshe" share one label
        if name not in name_map:
            name_map[name] = f"Customer {chr(ord('A') + len(name_map) % 26)}"
        return name_map[name]

    text, n = PHONE_RE.subn("[PHONE REDACTED]", text)
    count += n
    text, n = NRC_RE.subn("[NRC REDACTED]", text)
    count += n

    if mask_names:
        # Names seen before (e.g. in another source) are replaced wherever they appear
        for name, label in list(name_map.items()):
            text, n = re.subn(re.escape(name), label, text, flags=re.IGNORECASE)
            count += n

        def sub_honorific(m):
            nonlocal count
            count += 1
            return label_for(m.group(0))

        def sub_named_group(m):
            nonlocal count
            name = m.group(2)
            if name.split()[0].title() in NOT_A_NAME:
                return m.group(0)
            count += 1
            return f"{m.group(1)}{label_for(name)}"

        text = HONORIFIC_NAME_RE.sub(sub_honorific, text)
        text = WHATSAPP_SENDER_RE.sub(sub_named_group, text)
        text = PARTY_NAME_RE.sub(sub_named_group, text)

    return text, count
