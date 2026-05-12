"""
Centrale configuratie — één enkele bron van waarheid.
Pas hier aan bij nieuwe posities of DCA-datum.
"""
import os
from datetime import date

# ── Saxo credentials via omgevingsvariabelen (nooit hardcoden) ────────────────
SAXO_CLIENT_ID     = os.environ.get("SAXO_CLIENT_ID",     "48fa6a86736747b892805e8b68249340")
SAXO_CLIENT_SECRET = os.environ.get("SAXO_CLIENT_SECRET", "8878d3a0b23b4ea7b3e682d8400f9653")

# ── Email ─────────────────────────────────────────────────────────────────────
GMAIL_USER         = "jasper.jacobs04@gmail.com"
GMAIL_APP_PASSWORD = os.environ.get("GMAIL_APP_PASSWORD", "")

# ── Paden ─────────────────────────────────────────────────────────────────────
BASE_DIR      = r"C:\Users\jaspe\Documents\Investing_claude"
AGENTS_DIR    = os.path.join(BASE_DIR, "agents")
REPORTS_DIR   = os.path.join(AGENTS_DIR, "reports")
TOKEN_PATH    = os.path.join(BASE_DIR, "saxo_token.json")
FISCAL_FILE   = os.path.join(AGENTS_DIR, "fiscal_2026.json")
HISTORY_FILE  = os.path.join(AGENTS_DIR, "portfolio_history.json")
POSITIONS_FILE = os.path.join(AGENTS_DIR, "positions_config.json")

# ── Portefeuille (wordt dynamisch aangevuld in runtime) ───────────────────────
ETF_TICKER    = "IMIE.MI"
ETF_SHARES    = 671
ETF_INVESTED  = 6951.56   # historisch — voor rendementberekening
SAT_INVESTED  = 2188.48   # historisch — voor rendementberekening

# ── DCA — wordt automatisch voortgezet naar volgend kwartaal ─────────────────
DCA_START = date(2026, 5, 31)  # Eerste DCA-datum — systeem schuift automatisch 3 maanden op

def next_dca_date() -> date:
    """
    Geeft de eerstvolgende DCA-datum terug.
    Vertrekt van DCA_START en schuift telkens 3 maanden op totdat de datum in de toekomst ligt.
    Werkt correct over jaarsgrenzen en maanden met verschillende lengtes.
    """
    today = date.today()
    candidate = DCA_START
    while candidate <= today:
        # Voeg 3 maanden toe, respecteer maandeinden (bv. 31 aug → 30 nov)
        month = candidate.month + 3
        year  = candidate.year + (month - 1) // 12
        month = (month - 1) % 12 + 1
        # Begrens op laatste dag van de maand
        import calendar
        last_day = calendar.monthrange(year, month)[1]
        candidate = date(year, month, min(candidate.day, last_day))
    return candidate

# ── Posities (Saxo-symbool → Yahoo ticker + aankoopprijs) ────────────────────
POSITIONS = {
    "UCB:xbru":  {"ticker": "UCB.BR",  "cost": 255.50, "qty": 3, "currency": "EUR", "name": "UCB SA",            "sector": "Healthcare"},
    "HLNE:xnas": {"ticker": "HLNE",    "cost": 107.00, "qty": 6, "currency": "USD", "name": "Hamilton Lane",     "sector": "Financials"},
    "AOF:xetr":  {"ticker": "AOF.DE",  "cost": 79.80,  "qty": 6, "currency": "EUR", "name": "ATOSS Software AG", "sector": "Technology"},
    "V:xnys":    {"ticker": "V",       "cost": 296.50, "qty": 1, "currency": "USD", "name": "Visa Inc.",         "sector": "Financials"},
}

WATCHLIST = {
    "UNA.AS": {"name": "Unilever",           "sector": "Consumer Staples", "prio": 1, "hist_pe": 18.0},
    "SU.PA":  {"name": "Schneider Electric", "sector": "Industrials",      "prio": 2, "hist_pe": 22.0},
}

HIST_PE = {p["ticker"]: 25.0 for p in POSITIONS.values()} | {
    "UCB.BR": 25.0, "HLNE": 22.0, "AOF.DE": 32.0, "V": 27.0,
}
HIST_PE.update({t: v["hist_pe"] for t, v in WATCHLIST.items()})

# ── Drempelwaarden ────────────────────────────────────────────────────────────
EURUSD             = 1.12
STOP_LOSS_PCT      = 30.0
CONCENTRATION_MAX  = 35.0
EARNINGS_HORIZON   = 7
DCA_ALARM_DAGEN    = 21
FISCAL_ALARM_PCT   = 80.0
