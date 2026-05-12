"""
Saxo Bank API client met automatische token refresh.
Credentials komen uit config.py (nooit hardcoded).
"""
import json, os, time, urllib.parse, urllib.request, http.server, threading, webbrowser
from typing import Optional

from config import (SAXO_CLIENT_ID, SAXO_CLIENT_SECRET, TOKEN_PATH,
                    POSITIONS, HIST_PE, EURUSD, GMAIL_USER, GMAIL_APP_PASSWORD)

REDIRECT_URI = "http://localhost:5000/callback"
BASE_URL     = "https://gateway.saxobank.com/openapi"
AUTH_URL     = "https://live.logonvalidation.net/authorize"
TOKEN_URL    = "https://live.logonvalidation.net/token"

_auth_code = None


class _CallbackHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        global _auth_code
        params = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        if "code" in params:
            _auth_code = params["code"][0]
            self.send_response(200)
            self.send_header("Content-type", "text/html")
            self.end_headers()
            self.wfile.write(b"<h2>Ingelogd! Je kan dit venster sluiten.</h2>")
        else:
            self.send_response(400)
            self.end_headers()

    def log_message(self, *args):
        pass


def _post_token(data: dict) -> dict:
    body = urllib.parse.urlencode(data).encode()
    req = urllib.request.Request(
        TOKEN_URL, data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"}
    )
    try:
        with urllib.request.urlopen(req) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        raise Exception(f"Token fout {e.code}: {e.read().decode()}")


def _save(token_data: dict):
    token_data["saved_at"] = int(time.time())
    with open(TOKEN_PATH, "w", encoding="utf-8") as f:
        json.dump(token_data, f, indent=2)


def _refresh(token_data: dict) -> Optional[dict]:
    rt = token_data.get("refresh_token")
    if not rt:
        return None
    try:
        new_token = _post_token({
            "grant_type":    "refresh_token",
            "refresh_token": rt,
            "client_id":     SAXO_CLIENT_ID,
            "client_secret": SAXO_CLIENT_SECRET,
        })
        _save(new_token)
        print("Token vernieuwd via refresh.")
        return new_token
    except Exception as e:
        print(f"Refresh mislukt: {e}")
        return None


def _browser_auth() -> dict:
    global _auth_code
    _auth_code = None
    server = http.server.HTTPServer(("localhost", 5000), _CallbackHandler)
    t = threading.Thread(target=server.handle_request)
    t.start()
    params = urllib.parse.urlencode({
        "client_id": SAXO_CLIENT_ID,
        "response_type": "code",
        "redirect_uri": REDIRECT_URI,
    })
    print("Browser openen voor Saxo login...")
    webbrowser.open(f"{AUTH_URL}?{params}")
    t.join(timeout=120)
    server.server_close()
    if not _auth_code:
        raise Exception("Geen autorisatie ontvangen binnen 2 minuten.")
    token = _post_token({
        "grant_type":   "authorization_code",
        "code":         _auth_code,
        "redirect_uri": REDIRECT_URI,
        "client_id":    SAXO_CLIENT_ID,
        "client_secret": SAXO_CLIENT_SECRET,
    })
    _save(token)
    print(f"Nieuw token geldig voor {token.get('expires_in')} seconden.")
    return token


def _notify_reauth_needed():
    password = GMAIL_APP_PASSWORD.replace(" ", "")
    if not password:
        return
    try:
        import smtplib
        from email.message import EmailMessage
        msg = EmailMessage()
        msg["Subject"] = "Saxo login vereist — Portfolio Monitor"
        msg["From"]    = GMAIL_USER
        msg["To"]      = GMAIL_USER
        msg.set_content(
            "Je Saxo Bank sessie is volledig verlopen.\n\n"
            "Open Claude Code en voer uit:\n"
            "  python agents/daily_routine.py --force\n\n"
            "Er opent een browservenster voor herlogin."
        )
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as s:
            s.login(GMAIL_USER, password)
            s.send_message(msg)
        print("Notificatie verstuurd: herlogin vereist.")
    except Exception:
        pass


def get_token() -> str:
    if os.path.exists(TOKEN_PATH):
        with open(TOKEN_PATH, encoding="utf-8") as f:
            data = json.load(f)
        refreshed = _refresh(data)
        if refreshed:
            return refreshed["access_token"]
    _notify_reauth_needed()
    return _browser_auth()["access_token"]


def api_get(path: str, token: str) -> dict:
    req = urllib.request.Request(
        f"{BASE_URL}{path}",
        headers={"Authorization": f"Bearer {token}"}
    )
    try:
        with urllib.request.urlopen(req) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        raise Exception(f"HTTP {e.code}: {e.read().decode('utf-8', errors='replace')}")


def _yahoo_price(ticker: str) -> Optional[float]:
    enc = urllib.parse.quote(ticker)
    url = (f"https://query1.finance.yahoo.com/v8/finance/chart/"
           f"{enc}?interval=1d&range=1d")
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            data = json.loads(r.read())
        return round(data["chart"]["result"][0]["meta"]["regularMarketPrice"], 4)
    except Exception:
        return None


def get_portfolio() -> dict:
    token = get_token()

    accounts  = api_get("/port/v1/accounts/me", token)
    account   = accounts["Data"][0]
    enc_acc   = urllib.parse.quote(account["AccountKey"], safe="")
    enc_cli   = urllib.parse.quote(account["ClientKey"],  safe="")

    balance   = api_get(f"/port/v1/balances?AccountKey={enc_acc}&ClientKey={enc_cli}", token)
    positions = api_get(
        f"/port/v1/positions?AccountKey={enc_acc}&ClientKey={enc_cli}"
        "&FieldGroups=DisplayAndFormat,PositionBase,PositionView", token
    )

    holdings = []
    for p in positions.get("Data", []):
        base   = p.get("PositionBase", {})
        fmt    = p.get("DisplayAndFormat", {})
        symbol = fmt.get("Symbol", "?")
        cfg    = POSITIONS.get(symbol, {})
        ticker = cfg.get("ticker", symbol)
        qty    = base.get("Amount", 0)
        cost   = cfg.get("cost") or base.get("OpenPrice", 0)
        curr   = fmt.get("Currency", cfg.get("currency", ""))
        price  = _yahoo_price(ticker)
        stale  = price is None
        price  = price or cost  # fallback naar aankoopprijs indien Yahoo down

        eur_val = (price * qty) if curr == "EUR" else (price * qty / EURUSD)
        holdings.append({
            "symbol":   symbol,
            "name":     cfg.get("name", fmt.get("Description", symbol)),
            "sector":   cfg.get("sector", ""),
            "ticker":   ticker,
            "qty":      qty,
            "cost":     cost,
            "price":    price,
            "currency": curr,
            "eur_val":  round(eur_val, 2),
            "value":    round(price * qty, 2),
            "pnl_pct":  round((price - cost) / cost * 100, 2) if cost and not stale else None,
            "stale":    stale,
        })

    return {
        "account_id":  account.get("AccountId"),
        "total_value": balance.get("TotalValue", 0),
        "cash":        balance.get("CashBalance", 0),
        "holdings":    holdings,
    }
