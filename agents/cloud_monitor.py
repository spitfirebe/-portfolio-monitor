"""
Cloud Portfolio Monitor — draait op GitHub Actions, geen lokale computer nodig.
Gebruikt Yahoo Finance voor koersen (geen Saxo auth vereist).
Posities worden geladen uit positions_config.json.
"""
import json, os, smtplib, urllib.request, urllib.parse
from datetime import date, datetime
from email.message import EmailMessage

GMAIL_USER = "jasper.jacobs04@gmail.com"
CONFIG_PATH = os.path.join(os.path.dirname(__file__), "positions_config.json")

ETF_KERN     = 7305.85
ETF_INVESTED = 6951.56
SAT_INVESTED = 2188.48
EURUSD       = 1.12
NEXT_DCA     = date(2026, 7, 1)
STOP_LOSS_PCT = 30.0
CONCENTRATION_MAX = 35.0

SECTOR_MAP = {
    "UCB.BR": "Healthcare",
    "HLNE":   "Financials",
    "AOF.DE": "Technology",
    "V":      "Financials",
}
WATCHLIST = {
    "UNA.AS": {"name": "Unilever",           "sector": "Consumer Staples", "prio": 1},
    "SU.PA":  {"name": "Schneider Electric", "sector": "Industrials",      "prio": 2},
}
HIST_PE = {
    "UCB.BR": 25.0, "HLNE": 22.0, "AOF.DE": 32.0, "V": 27.0,
    "UNA.AS": 18.0, "SU.PA": 22.0,
}

def get_price(ticker):
    enc = urllib.parse.quote(ticker)
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{enc}?interval=1d&range=1d"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(req, timeout=8) as r:
            d = json.loads(r.read())
        return round(d["chart"]["result"][0]["meta"]["regularMarketPrice"], 4)
    except Exception:
        return None

def get_forward_pe(ticker):
    enc = urllib.parse.quote(ticker)
    url = f"https://query1.finance.yahoo.com/v7/finance/quote?symbols={enc}"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(req, timeout=8) as r:
            d = json.loads(r.read())
        return d.get("quoteResponse", {}).get("result", [{}])[0].get("forwardPE")
    except Exception:
        return None

def load_positions():
    with open(CONFIG_PATH, encoding="utf-8") as f:
        return json.load(f)

def build_and_send():
    today = date.today()
    positions = load_positions()
    dagen_dca = (NEXT_DCA - today).days

    # Koersen ophalen
    holdings = []
    for p in positions:
        ticker = p["ticker"]
        price  = get_price(ticker) or p["cost"]
        fpe    = get_forward_pe(ticker)
        value  = round(price * p["qty"], 2)
        sl     = round(p["cost"] * (1 - STOP_LOSS_PCT / 100), 2)
        sl_buf = round((price - sl) * p["qty"], 2)
        holdings.append({**p, "price": price, "value": value,
                         "fpe": fpe, "sl": sl, "sl_buf": sl_buf})

    sat_val = sum(h["value"] if h["currency"] == "EUR"
                  else h["value"] / EURUSD for h in holdings)
    totaal  = sat_val + ETF_KERN
    etf_pct = round(ETF_KERN / totaal * 100, 1)
    sat_pct = round(sat_val  / totaal * 100, 1)

    # Triggers
    triggers = []
    for h in holdings:
        pct = round((h["value"] if h["currency"] == "EUR" else h["value"] / EURUSD) / sat_val * 100, 1)
        if pct > CONCENTRATION_MAX:
            triggers.append(f"Concentratie: {h['name']} is {pct}% van satelliet (drempel {CONCENTRATION_MAX}%)")
    if dagen_dca <= 21:
        triggers.append(f"DCA nadert: nog {dagen_dca} dagen ({NEXT_DCA})")

    # Watchlist koersen
    watchlist_data = []
    for ticker, info in WATCHLIST.items():
        price = get_price(ticker)
        fpe   = get_forward_pe(ticker)
        hist  = HIST_PE.get(ticker)
        status = ""
        if fpe and hist:
            delta = round((fpe / hist - 1) * 100, 1)
            status = f"KOOPZONE ({delta:+.1f}%)" if fpe < hist else f"{delta:+.1f}% boven gem."
            if fpe < hist:
                triggers.append(f"Koopzone: {info['name']} Forward P/E {fpe:.1f}x < hist. gem. {hist}x")
        watchlist_data.append({**info, "ticker": ticker, "price": price,
                                "fpe": fpe, "hist": hist, "status": status})

    # Email bouwen
    trigger_html = ""
    if triggers:
        trigger_html = "<h3 style='color:#c62828'>Actieve signalen</h3>"
        for t in triggers:
            trigger_html += f"<div style='background:#fff3e0;border-left:4px solid #e65100;padding:10px;margin:4px 0;border-radius:0 6px 6px 0'>{t}</div>"

    positie_rijen = ""
    for h in sorted(holdings, key=lambda x: x["value"], reverse=True):
        pct = round((h["value"] if h["currency"] == "EUR" else h["value"] / EURUSD) / sat_val * 100, 1)
        bg  = "#ffebee" if pct > CONCENTRATION_MAX else ""
        positie_rijen += f"""<tr style="background:{bg}">
          <td><strong>{h['name']}</strong><br><small style='color:#666'>{h['ticker']} · {SECTOR_MAP.get(h['ticker'],'')}</small></td>
          <td align='right'>{h['qty']}</td>
          <td align='right'><strong>{h['price']:.2f} {h['currency']}</strong></td>
          <td align='right'>{h['value']:,.2f} {h['currency']}</td>
          <td align='right'>{pct}%</td>
          <td align='right' style='font-size:12px'>nog {h['currency']} {h['sl_buf']:,.0f}<br><small style='color:#666'>(buf. tot Zanna -30%)</small></td>
        </tr>"""

    wl_rijen = ""
    for w in watchlist_data:
        fpe_str  = f"{w['fpe']:.1f}x" if w["fpe"] else "—"
        hist_str = f"{w['hist']:.1f}x" if w["hist"] else "—"
        prijs_str = f"{w['price']:.2f}" if w["price"] else "—"
        kleur = "color:#2e7d32;font-weight:bold" if "KOOPZONE" in w.get("status","") else ""
        wl_rijen += f"<tr><td>#{w['prio']} {w['name']} ({w['ticker']})</td><td align='right'>{prijs_str}</td><td align='right'>{fpe_str}</td><td align='right'>{hist_str}</td><td style='{kleur}'>{w.get('status','—')}</td></tr>"

    etf_ret = round((ETF_KERN - ETF_INVESTED) / ETF_INVESTED * 100, 2)
    sat_ret = round((sat_val  - SAT_INVESTED)  / SAT_INVESTED  * 100, 2)

    html = f"""<!DOCTYPE html><html><body style="font-family:Arial,sans-serif;max-width:680px;margin:auto;color:#333;font-size:14px">
<h2 style="color:#1565c0">Portfolio Slotkoersen — {today.strftime('%d/%m/%Y')}</h2>
<p style="color:#888;font-size:12px">Cloud agent (GitHub Actions) · na sluiting NYSE/NASDAQ 22:15 CET</p>

<table width="100%" cellpadding="10" style="background:#e3f2fd;border-radius:8px;margin-bottom:16px;border-collapse:collapse">
<tr>
  <td><div style="font-size:11px;color:#666">TOTAAL</div><div style="font-size:20px;font-weight:bold;color:#1565c0">€{totaal:,.2f}</div></td>
  <td><div style="font-size:11px;color:#666">ETF KERN</div><div style="font-size:15px">€{ETF_KERN:,.2f} ({etf_pct}%)</div></td>
  <td><div style="font-size:11px;color:#666">SATELLIET</div><div style="font-size:15px">€{sat_val:,.2f} ({sat_pct}%)</div></td>
  <td><div style="font-size:11px;color:#666">DCA</div><div style="font-size:15px;{'color:#c62828;font-weight:bold' if dagen_dca<=21 else ''}">{dagen_dca}d</div></td>
</tr>
</table>

{trigger_html}

<h3>Posities (slotkoersen)</h3>
<table width="100%" cellpadding="6" cellspacing="0" style="border-collapse:collapse;font-size:13px">
<tr style="background:#f5f5f5"><th align="left">Positie</th><th align="right">Qty</th><th align="right">Slotkoers</th><th align="right">Waarde</th><th align="right">% Sat.</th><th align="right">Stop-buffer</th></tr>
{positie_rijen}
</table>

<h3 style="margin-top:20px">Watchlist — 90-sec check</h3>
<table width="100%" cellpadding="6" cellspacing="0" style="border-collapse:collapse;font-size:13px">
<tr style="background:#f5f5f5"><th align="left">Bedrijf</th><th align="right">Koers</th><th align="right">Fwd P/E</th><th align="right">Hist. gem.</th><th align="left">Status</th></tr>
{wl_rijen}
</table>

<h3 style="margin-top:20px">Performance sinds start</h3>
<table width="100%" cellpadding="8" style="border-collapse:collapse;font-size:13px">
<tr>
  <td style="background:#f5f5f5;width:50%"><div style="font-size:11px;color:#666">ETF KERN (sinds 27/02/2026)</div>
    <div style="font-size:20px;color:#2e7d32;font-weight:bold">{etf_ret:+.2f}%</div></td>
  <td style="background:#f5f5f5;width:50%;padding-left:12px"><div style="font-size:11px;color:#666">SATELLIET (sinds 02/04/2026)</div>
    <div style="font-size:20px;color:{'#2e7d32' if sat_ret>=0 else '#c62828'};font-weight:bold">{sat_ret:+.2f}%</div></td>
</tr>
</table>
<p style="font-size:11px;color:#999;font-style:italic">Vergelijk pas na 3+ jaar (PP1). Kortetermijn underperformance is normaal bij kwaliteitsbeleggen.</p>

<hr style="margin:16px 0;border:none;border-top:1px solid #e0e0e0">
<p style="color:#bbb;font-size:11px">Cloud Portfolio Monitor · GitHub Actions · {today}</p>
</body></html>"""

    subject = f"Slotkoersen {today.strftime('%d/%m/%Y')}"
    if triggers:
        subject = f"[ALERT] Slotkoersen {today.strftime('%d/%m/%Y')} — {len(triggers)} signaal(en)"

    password = os.environ.get("GMAIL_APP_PASSWORD", "").replace(" ", "")
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"]    = GMAIL_USER
    msg["To"]      = GMAIL_USER
    msg.set_content("Open in HTML viewer voor opmaak.")
    msg.add_alternative(html, subtype="html")
    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as s:
        s.login(GMAIL_USER, password)
        s.send_message(msg)
    print(f"Verstuurd: {subject}")

if __name__ == "__main__":
    build_and_send()
