"""Data sources: simulated MTN MoMo, Airtel Money and WhatsApp feeds, and what counts as verified."""
import random
import re


SAMPLE_DATA = (
    "Mon: Received 4,000 ZMW from Mama Chileshe for 2 bags of mealie meal.\n"
    "Mon: Paid 1,500 ZMW for transport to Lusaka.\n"
    "Tue: Received 2,000 ZMW from Mr. Phiri via MTN MoMo, ref 0971234567.\n"
    "Wed: Paid 800 ZMW to supplier for cooking oil.\n"
    "Thu: Received 1,200 ZMW from Bashi Mwansa.\n"
    "Fri: I still owe the supplier 1,000 ZMW, will pay next week."
)


# Simulated provider feeds - stand-ins for the MTN MoMo, Airtel Money and WhatsApp Business APIs.
# The WhatsApp chat deliberately overlaps the MoMo record so the agent has to de-duplicate.
SIMULATED_FEEDS = {
    # Six months of wallet history (April - October), so the demo business passes the continuity rule
    "MTN MoMo": (
        "MTN MoMo: You have received ZMW 2,600.00 from CHANDA MULENGA (0965550101) on 14/04/2026 10:05. Txn ID 7710233\n"
        "MTN MoMo: You have received ZMW 2,350.00 from PRISCA BANDA (0965550102) on 09/05/2026 14:20. Txn ID 7745120\n"
        "MTN MoMo: You have sent ZMW 1,200.00 to KAFUE MILLING (0966550200) on 18/05/2026 08:45. Txn ID 7751904\n"
        "MTN MoMo: You have received ZMW 3,100.00 from MUSONDA PHIRI (0765550103) on 06/06/2026 11:32. Txn ID 7789341\n"
        "MTN MoMo: You have received ZMW 2,950.00 from NATASHA ZULU (0965550104) on 09/07/2026 16:10. Txn ID 7820457\n"
        "MTN MoMo: You have sent ZMW 1,400.00 to KAFUE MILLING (0966550200) on 24/07/2026 09:02. Txn ID 7835112\n"
        "MTN MoMo: You have received ZMW 3,300.00 from KELVIN TEMBO (0965550105) on 11/08/2026 13:47. Txn ID 7868230\n"
        "MTN MoMo: You have received ZMW 2,700.00 from ESTHER MWALE (0765550106) on 02/09/2026 10:15. Txn ID 7901786\n"
        "MTN MoMo: You have sent ZMW 1,250.00 to KAFUE MILLING (0966550200) on 19/09/2026 08:30. Txn ID 7918845\n"
        "MTN MoMo: You have received ZMW 4,000.00 from MAMA CHILESHE (0961234567) on 20/10/2026 09:12. Txn ID 8812734. Bal ZMW 5,240.00\n"
        "MTN MoMo: You have sent ZMW 1,500.00 to KALULU TRANSPORT (0966554433) on 20/10/2026 11:40. Txn ID 8812901. Bal ZMW 3,740.00\n"
        "MTN MoMo: You have received ZMW 2,000.00 from JOSEPH PHIRI (0767889900) on 21/10/2026 15:03. Txn ID 8813550. Bal ZMW 5,740.00\n"
        "MTN MoMo: Payment of ZMW 350.00 to ZESCO prepaid electricity on 22/10/2026 08:05. Txn ID 8814002. Bal ZMW 5,390.00"
    ),
    "Airtel Money": (
        "Airtel Money: You have received K1,500.00 from LUBASI NYIRENDA 0977881122 on 16/06/2026. TID: PP260616.1105.B21\n"
        "Airtel Money: You have paid K600.00 for FUEL at Merchant 40213 on 30/06/2026. TID: PP260630.1712.C04\n"
        "Airtel Money: You have received K1,800.00 from TAONGA BWALYA 0977334455 on 14/08/2026. TID: PP260814.0950.A17\n"
        "Airtel Money: You have received K1,200.00 from BASHI MWANSA 0977123456 on 23/10/2026. TID: PP261023.1422.A55\n"
        "Airtel Money: You have paid K650.00 for FUEL at Merchant 40213 on 23/10/2026. TID: PP261023.1610.C12\n"
        "Airtel Money: You have sent K800.00 to MWILA WHOLESALERS 0975432100 on 24/10/2026. TID: PP261024.0931.D07"
    ),
    "WhatsApp": (
        "[20/10/2026, 08:55] Mama Chileshe: Hello, I've sent the 4,000 on MoMo for the 2 bags of mealie meal\n"
        "[21/10/2026, 19:40] Mama Chileshe: Are you coming to the kitchen party on Saturday?\n"
        "[22/10/2026, 17:30] Mwila Wholesalers: Reminder: balance of K1,000 for the cooking oil is still outstanding, please pay by Friday\n"
        "[23/10/2026, 07:15] Grace Banda: Good morning! How is your mother feeling today?\n"
        "[24/10/2026, 12:10] Grace Banda: Can you deliver 3 bags tomorrow? I'll pay K3,000 cash on delivery\n"
        "[25/10/2026, 10:02] Grace Banda: Received the bags, I've paid you the K3,000 cash. Thank you!"
    ),
}
SOURCES = (*SIMULATED_FEEDS, "Pasted text", "Image")
VERIFIED_SOURCES = {"MTN MoMo", "Airtel Money"}


def evidence_class(source):
    """Primary evidence comes machine-to-machine from a wallet API and is what a loan rests on.
    Supporting evidence (receipts, chats, pasted text) is customer-provided context: it can be faked,
    so it never approves a loan on its own."""
    return "Primary" if source in VERIFIED_SOURCES else "Supporting"

# Which network a linked number must be on (None = any network)
PROVIDER_NETWORK = {"MTN MoMo": "MTN", "Airtel Money": "Airtel", "WhatsApp": None}
LINK_HELP = {
    "MTN MoMo": "Use the MTN number (096 or 076) that your MoMo wallet is registered to.",
    "Airtel Money": "Use the Airtel number (097 or 077) that your Airtel Money wallet is registered to.",
    "WhatsApp": "Use the number of your WhatsApp Business account. Only messages about money are shared - "
                "personal messages are removed before anything leaves this portal.",
}

# Chat messages are only shared when they are about money (data minimisation)
CHAT_PREFIX_RE = re.compile(r"^\[[^\]]*\]\s*[^:]{1,60}:\s*")  # "[20/10/2026, 08:55] Name: "
MONEY_RE = re.compile(
    r"\b(?:zmw|kwacha|k\s?\d[\d,]*|paid|pay|payment|sent|send|received|receive|owe|owed|owing|invoice|"
    r"balance|deposit|loan|debt|credit|cash|bought|sold|price|cost|order|supplier)\b|\d{3,}",
    re.IGNORECASE,
)


def keep_money_messages(text):
    """Keep only lines about money; return (kept text, number of messages removed)."""
    lines = [line for line in text.splitlines() if line.strip()]
    kept = [line for line in lines if MONEY_RE.search(CHAT_PREFIX_RE.sub("", line))]
    return "\n".join(kept), len(lines) - len(kept)


MAX_NUMBERS_PER_PROVIDER = 3
SAMPLE_CUSTOMERS = ("CHANDA MULENGA", "PRISCA BANDA", "MUSONDA PHIRI", "NATASHA ZULU", "KELVIN TEMBO", "ESTHER MWALE")
SAMPLE_SUPPLIERS = ("LUSAKA FRESH PRODUCE", "KAFUE MILLING", "COMESA MARKET TRADERS")


def simulated_feed(provider, seed):
    """Sample history for an extra linked number. Seeded by the number, so it is stable and unique."""
    rng = random.Random(seed)
    # Spread over different months between April and October 2026
    days = [f"{rng.randint(1, 28):02d}/{m:02d}/2026" for m in sorted(rng.sample(range(4, 11), 4))]
    amounts = [rng.randrange(400, 3200, 50) for _ in range(4)]
    customers = rng.sample(SAMPLE_CUSTOMERS, 3)
    supplier = rng.choice(SAMPLE_SUPPLIERS)
    if provider == "WhatsApp":
        return "\n".join([
            f"[{days[0]}, 09:{rng.randrange(10, 59)}] {customers[0].title()}: I've sent K{amounts[0]:,} for the order",
            f"[{days[1]}, 18:{rng.randrange(10, 59)}] {customers[1].title()}: Happy Sunday! See you at church",
            f"[{days[2]}, 11:{rng.randrange(10, 59)}] {supplier.title()}: Your invoice of K{amounts[3]:,} "
            "for stock is due on Friday",
        ])
    name, cur = ("MTN MoMo", "ZMW ") if provider == "MTN MoMo" else ("Airtel Money", "K")
    lines = [f"{name}: You have received {cur}{amounts[i]:,.2f} from {customers[i]} on {days[i]}. "
             f"Txn ID {rng.randrange(10**7, 10**8)}" for i in range(3)]
    lines.append(f"{name}: You have sent {cur}{amounts[3]:,.2f} to {supplier} on {days[3]}. "
                 f"Txn ID {rng.randrange(10**7, 10**8)}")
    return "\n".join(lines)
