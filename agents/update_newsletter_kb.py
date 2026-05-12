"""
Newsletter KB Agent — leest emails van De Kwaliteitsbelegger via IMAP
en voegt nieuwe inhoudelijke content toe aan KB11_newsletter_inzichten.md.

Draait op GitHub Actions: elke dag om 08:30 CET (07:30 UTC).
Vereist: GMAIL_APP_PASSWORD omgevingsvariabele.
"""
import imaplib, email, os, re, json, sys
from datetime import date, datetime, timedelta
from email.header import decode_header

GMAIL_USER     = "jasper.jacobs04@gmail.com"
SENDER         = "dekwaliteitsbelegger@substack.com"
KB11_PATH      = os.path.join(os.path.dirname(__file__), "..", "Kennis", "KB11_newsletter_inzichten.md")
STATE_FILE     = os.path.join(os.path.dirname(__file__), "newsletter_state.json")

# Emails met deze woorden in het onderwerp zijn puur promotioneel → overslaan
PROMO_KEYWORDS = [
    "korting", "uur over", "nog slechts", "aanbieding", "gratis",
    "betaal", "abonneer", "founding partner", "% korting", "8 uur",
    "24 uur", "laatste kans", "zegt dat je"
]

# Minimum aantal woorden in de body om als inhoudelijk te beschouwen
MIN_WOORDEN = 200


def load_state() -> dict:
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, encoding="utf-8") as f:
            return json.load(f)
    return {"last_processed": "2026-05-12", "processed_ids": []}


def save_state(state: dict):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)


def decode_str(s) -> str:
    if isinstance(s, bytes):
        return s.decode("utf-8", errors="replace")
    parts = decode_header(s)
    result = []
    for part, enc in parts:
        if isinstance(part, bytes):
            result.append(part.decode(enc or "utf-8", errors="replace"))
        else:
            result.append(str(part))
    return " ".join(result)


def is_promotional(subject: str) -> bool:
    subject_lower = subject.lower()
    return any(kw in subject_lower for kw in PROMO_KEYWORDS)


def extract_text(msg) -> str:
    """Haalt plain text body op uit email."""
    text = ""
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == "text/plain":
                payload = part.get_payload(decode=True)
                if payload:
                    text += payload.decode("utf-8", errors="replace")
    else:
        payload = msg.get_payload(decode=True)
        if payload:
            text = payload.decode("utf-8", errors="replace")
    # Verwijder URL's en tracking codes
    text = re.sub(r'https?://\S+', '', text)
    text = re.sub(r'\r\n', '\n', text)
    text = re.sub(r'\n{3,}', '\n\n', text)
    # Verwijder "unsubscribe" footer
    text = re.split(r'Unsubscribe|Geef je op voor', text)[0]
    return text.strip()


def format_for_kb(subject: str, datum: str, body: str) -> str:
    """Formatteert een email als Markdown sectie voor KB11."""
    # Verwijder overtollige witruimte
    body = re.sub(r'\n{3,}', '\n\n', body)
    # Beperk tot max 3000 tekens om KB11 beheersbaar te houden
    if len(body) > 3000:
        body = body[:3000] + "\n\n*[Volledige tekst beschikbaar via Substack]*"

    return f"""
---

## {subject}
**Datum:** {datum}
**Bron:** De Kwaliteitsbelegger (Pieter Slegers)

{body}
"""


def update_log(kb_path: str, subject: str, datum: str):
    """Voegt een rij toe aan de Update-log tabel in KB11."""
    with open(kb_path, encoding="utf-8") as f:
        content = f.read()

    log_marker = "| Datum | Onderwerp | Bron |"
    if log_marker in content:
        new_row = f"| {datum} | {subject} | Slegers newsletter |"
        # Voeg na de header + separator rij in
        content = content.replace(
            "|-------|-----------|------|",
            f"|-------|-----------|------|\n{new_row}"
        )
        with open(kb_path, "w", encoding="utf-8") as f:
            f.write(content)


def main():
    app_password = os.environ.get("GMAIL_APP_PASSWORD", "").replace(" ", "")
    if not app_password:
        print("FOUT: GMAIL_APP_PASSWORD niet ingesteld.")
        sys.exit(1)

    state = load_state()
    last_date = datetime.strptime(state["last_processed"], "%Y-%m-%d").date()
    search_since = (last_date - timedelta(days=1)).strftime("%d-%b-%Y")

    print(f"Verbinden met Gmail IMAP...")
    mail = imaplib.IMAP4_SSL("imap.gmail.com")
    mail.login(GMAIL_USER, app_password)
    mail.select("inbox")

    typ, msgs = mail.search(None, f'(FROM "{SENDER}" SINCE "{search_since}")')
    msg_ids = msgs[0].split()
    print(f"  {len(msg_ids)} emails gevonden van {SENDER} sinds {search_since}")

    nieuw_verwerkt = 0
    kb_additions = []
    today_str = date.today().strftime("%d/%m/%Y")

    for msg_id in msg_ids:
        msg_id_str = msg_id.decode()
        if msg_id_str in state["processed_ids"]:
            continue

        typ, data = mail.fetch(msg_id, "(RFC822)")
        raw = data[0][1]
        msg = email.message_from_bytes(raw)

        subject = decode_str(msg.get("Subject", ""))
        datum   = decode_str(msg.get("Date", ""))

        # Datum parsen voor sortering
        try:
            parsed_date = email.utils.parsedate_to_datetime(datum).date()
            datum_str   = parsed_date.strftime("%d/%m/%Y")
        except Exception:
            datum_str = today_str

        print(f"  [{datum_str}] {subject}")

        # Promotioneel → overslaan
        if is_promotional(subject):
            print(f"    -> Overgeslagen (promotioneel)")
            state["processed_ids"].append(msg_id_str)
            continue

        body = extract_text(msg)
        word_count = len(body.split())

        if word_count < MIN_WOORDEN:
            print(f"    -> Overgeslagen (te kort: {word_count} woorden)")
            state["processed_ids"].append(msg_id_str)
            continue

        print(f"    -> Verwerkt ({word_count} woorden)")
        kb_additions.append(format_for_kb(subject, datum_str, body))
        update_log(KB11_PATH, subject, datum_str)
        state["processed_ids"].append(msg_id_str)
        nieuw_verwerkt += 1

    mail.logout()

    if kb_additions:
        with open(KB11_PATH, "a", encoding="utf-8") as f:
            f.write("\n".join(kb_additions))
        print(f"\n{nieuw_verwerkt} nieuwe artikelen toegevoegd aan KB11.")
    else:
        print("\nGeen nieuwe inhoudelijke emails.")

    state["last_processed"] = date.today().strftime("%Y-%m-%d")
    # Houd processed_ids beheersbaar (max 500)
    state["processed_ids"] = state["processed_ids"][-500:]
    save_state(state)


if __name__ == "__main__":
    main()
