"""
Maandelijkse DCA herinnering — draait op 1e van de maand via Task Scheduler.
"""
import sys, os, smtplib
from datetime import date
from email.message import EmailMessage

sys.path.insert(0, os.path.dirname(__file__))
from config import next_dca_date, CAGR_AANNAMES, GMAIL_USER, GMAIL_APP_PASSWORD

def main():
    dca   = next_dca_date()
    dagen = (dca - date.today()).days
    dca_bedrag = CAGR_AANNAMES["dca_nu"]

    subject = f"[DCA] Volgende inleg: {dca.strftime('%d/%m/%Y')} ({dagen} dagen)"
    body = (
        f"Volgende DCA: {dca.strftime('%d/%m/%Y')} — over {dagen} dagen\n\n"
        f"Bedrag: EUR {dca_bedrag} (kwartaals)\n"
        f"  80% IMIE.MI via Bolero  → EUR {round(dca_bedrag * 0.8)}\n"
        f"  20% Slegers kandidaat  → EUR {round(dca_bedrag * 0.2)}\n\n"
        f"Stap 1: run screener voor nieuwe watchlist kandidaten\n"
        f"Stap 2: controleer EGM drempel (>= 10%)\n"
        f"Stap 3: voer order uit voor {dca.strftime('%d/%m/%Y')}\n"
    )

    password = GMAIL_APP_PASSWORD.replace(" ", "")
    if not password:
        print("FOUT: GMAIL_APP_PASSWORD niet ingesteld.")
        return

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"]    = GMAIL_USER
    msg["To"]      = GMAIL_USER
    msg.set_content(body)

    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as s:
            s.login(GMAIL_USER, password)
            s.send_message(msg)
        print(f"DCA reminder verstuurd: {subject}")
    except Exception as e:
        print(f"Email mislukt: {e}")

if __name__ == "__main__":
    main()
