"""
Portfolio Monitor v2 — trigger-gebaseerd, anti-anchoring.
Stuurt email ALLEEN bij actief signaal (of elke maandag).
"""
import sys, os, json, smtplib
from datetime import date, datetime
from email.message import EmailMessage
import urllib.request, urllib.parse
from typing import Optional

sys.path.insert(0, os.path.dirname(__file__))
from config import (
    GMAIL_USER, GMAIL_APP_PASSWORD, REPORTS_DIR, FISCAL_FILE, HISTORY_FILE,
    AGENTS_DIR, ETF_TICKER, ETF_SHARES, ETF_INVESTED, SAT_INVESTED,
    POSITIONS, WATCHLIST, HIST_PE, EURUSD,
    STOP_LOSS_PCT, CONCENTRATION_MAX, EARNINGS_HORIZON, DCA_ALARM_DAGEN,
    FISCAL_ALARM_PCT, next_dca_date,
)
from saxo_client import get_portfolio

EARNINGS_CONFIG_FILE = os.path.join(AGENTS_DIR, "earnings_config.json")

os.makedirs(REPORTS_DIR, exist_ok=True)


# ── Yahoo Finance ─────────────────────────────────────────────────────────────
def _yf_price(ticker: str) -> Optional[float]:
    enc = urllib.parse.quote(ticker)
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{enc}?interval=1d&range=1d"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(req, timeout=8) as r:
            d = json.loads(r.read())
        return round(d["chart"]["result"][0]["meta"]["regularMarketPrice"], 4)
    except Exception:
        return None


def _load_earnings_config() -> dict:
    """Laadt handmatig bijgehouden earnings datums."""
    if os.path.exists(EARNINGS_CONFIG_FILE):
        with open(EARNINGS_CONFIG_FILE, encoding="utf-8") as f:
            return json.load(f).get("earnings", {})
    return {}


def _yf_forward_pe_timeseries(ticker: str) -> Optional[float]:
    """
    Haalt Forward P/E op via Yahoo Finance fundamentals-timeseries.
    Werkt voor zowel US als Europese aandelen (UCB.BR, AOF.DE, etc.)
    """
    enc = urllib.parse.quote(ticker)
    url = (f"https://query1.finance.yahoo.com/ws/fundamentals-timeseries/v1/finance/"
           f"timeseries/{enc}?type=trailingForwardPeRatio,annualForwardPeRatio"
           f"&period1=1700000000&period2=1800000000")
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(req, timeout=8) as r:
            d = json.loads(r.read())
        for result in d.get("timeseries", {}).get("result", []):
            vals = (result.get("trailingForwardPeRatio")
                    or result.get("annualForwardPeRatio") or [])
            if vals:
                last = vals[-1]
                if isinstance(last, dict):
                    raw = last.get("reportedValue", {})
                    pe  = raw.get("raw") if isinstance(raw, dict) else raw
                    if pe:
                        return round(float(pe), 2)
    except Exception:
        pass
    return None


def _yf_valuation(ticker: str) -> dict:
    """
    Haalt Forward P/E en earnings datum op.
    Forward P/E: Yahoo Finance timeseries (werkt voor alle aandelen).
    Earnings datum: earnings_config.json (handmatig bijgehouden, altijd accuraat).
    """
    result = {"forward_pe": None, "earnings_date": None,
              "earnings_fmt": "onbekend", "earnings_label": ""}

    # Forward P/E via timeseries (werkt voor EU + US)
    result["forward_pe"] = _yf_forward_pe_timeseries(ticker)

    # Earnings datum via config
    earnings_cfg = _load_earnings_config()
    cfg = earnings_cfg.get(ticker, {})
    if cfg.get("date"):
        try:
            result["earnings_date"]  = datetime.strptime(cfg["date"], "%Y-%m-%d").date()
            result["earnings_fmt"]   = result["earnings_date"].strftime("%d/%m/%Y")
            result["earnings_label"] = cfg.get("label", "")
        except ValueError:
            pass

    return result


# ── ETF waarde dynamisch ──────────────────────────────────────────────────────
def get_etf_value() -> float:
    price = _yf_price(ETF_TICKER)
    if price:
        return round(price * ETF_SHARES, 2)
    return round(ETF_INVESTED * 1.05, 2)  # noodschatting +5% als Yahoo down is


# ── Fiscale data ──────────────────────────────────────────────────────────────
def load_fiscal() -> dict:
    if os.path.exists(FISCAL_FILE):
        with open(FISCAL_FILE, encoding="utf-8") as f:
            return json.load(f)
    return {"gerealiseerde_meerwaarde": 0, "tob_betaald": 0,
            "dividenden_ontvangen": 0, "aankopen": [], "dividenden": []}


def fiscal_tob_ytd(fiscal: dict) -> float:
    return sum(a.get("tob", 0) for a in fiscal.get("aankopen", []))


# ── Portfolio history ─────────────────────────────────────────────────────────
def update_history(totaal: float) -> dict:
    history = {}
    if os.path.exists(HISTORY_FILE):
        with open(HISTORY_FILE, encoding="utf-8") as f:
            history = json.load(f)
    history[str(date.today())] = round(totaal, 2)
    with open(HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(history, f, indent=2)
    return history


def history_sparkline(history: dict) -> str:
    items = sorted(history.items())[-10:]
    if len(items) < 2:
        return ""
    vals = [v for _, v in items]
    mn, mx = min(vals), max(vals)
    if mx == mn:
        return "-" * len(vals)
    chars = "________"  # ASCII-safe voor email subject
    return "".join(chars[int((v - mn) / (mx - mn) * 7)] for v in vals)


# ── Triggers ──────────────────────────────────────────────────────────────────
def check_triggers(holdings: list, market: dict, fiscal: dict,
                   sat_val_eur: float) -> list:
    triggers = []
    today = date.today()
    next_dca = next_dca_date()
    dagen_dca = (next_dca - today).days

    # 1. Earnings binnen horizon
    for h in holdings:
        ticker = h["ticker"]
        ed = market.get(ticker, {}).get("earnings_date")
        if ed and isinstance(ed, date):
            dagen = (ed - today).days
            if 0 <= dagen <= EARNINGS_HORIZON:
                triggers.append({
                    "type": "earnings", "urgent": dagen <= 3,
                    "label": f"Earnings {h['name']} over {dagen} dagen ({ed.strftime('%d/%m/%Y')})",
                })

    # 2. Concentratie (in EUR, correct omgerekend)
    for h in holdings:
        pct = round(h["eur_val"] / sat_val_eur * 100, 1) if sat_val_eur else 0
        if pct > CONCENTRATION_MAX:
            triggers.append({
                "type": "concentratie", "urgent": pct > 40,
                "label": f"{h['name']} is {pct}% van satelliet (drempel {CONCENTRATION_MAX}%)",
            })

    # 3. Watchlist koopzone
    for ticker, info in WATCHLIST.items():
        fpe  = market.get(ticker, {}).get("forward_pe")
        hist = info.get("hist_pe") or HIST_PE.get(ticker)
        if fpe and hist and fpe < hist:
            korting = round((1 - fpe / hist) * 100, 1)
            triggers.append({
                "type": "koopzone", "urgent": korting > 10,
                "label": f"{info['name']}: Fwd P/E {fpe:.1f}x vs hist. {hist}x ({korting}% korting)",
            })

    # 4. DCA nadert
    if dagen_dca <= DCA_ALARM_DAGEN:
        triggers.append({
            "type": "dca", "urgent": dagen_dca <= 7,
            "label": f"Volgende DCA over {dagen_dca} dagen ({next_dca.strftime('%d/%m/%Y')})",
        })

    # 5. Fiscale vrijstelling
    meerwaarde = fiscal.get("gerealiseerde_meerwaarde", 0)
    if meerwaarde > (10000 * FISCAL_ALARM_PCT / 100):
        triggers.append({
            "type": "fiscaal", "urgent": meerwaarde > 9000,
            "label": f"Gerealiseerde meerwaarde {meerwaarde:,.2f} EUR / 10.000 EUR vrijstelling",
        })

    return triggers


def should_send(triggers: list) -> bool:
    return date.today().weekday() == 0 or len(triggers) > 0


# ── Email bouwen ──────────────────────────────────────────────────────────────
def build_html(holdings: list, market: dict, fiscal: dict, triggers: list,
               history: dict, etf_kern: float) -> tuple:
    today     = date.today().strftime("%d/%m/%Y")
    next_dca  = next_dca_date()
    dagen_dca = (next_dca - date.today()).days

    sat_val_eur = sum(h["eur_val"] for h in holdings)
    totaal      = sat_val_eur + etf_kern
    etf_pct     = round(etf_kern / totaal * 100, 1)
    sat_pct     = round(sat_val_eur / totaal * 100, 1)
    etf_return  = round((etf_kern - ETF_INVESTED)   / ETF_INVESTED  * 100, 2)
    sat_return  = round((sat_val_eur - SAT_INVESTED) / SAT_INVESTED * 100, 2)

    meerwaarde      = fiscal.get("gerealiseerde_meerwaarde", 0)
    tob_ytd         = fiscal_tob_ytd(fiscal)
    dividenden      = fiscal.get("dividenden_ontvangen", 0)
    vrijstelling_pct = round(meerwaarde / 10000 * 100, 1)

    # Subject (geen emojis — SMTP-safe)
    urgente = [t for t in triggers if t.get("urgent")]
    if urgente:
        subject = f"[ALERT] Portfolio {today} — {urgente[0]['label'][:55]}"
    elif triggers:
        subject = f"[SIGNAAL] Portfolio Monitor {today} — {len(triggers)} punt(en)"
    else:
        subject = f"Portfolio Monitor {today} — Maandelijks overzicht"

    # Trigger blokken
    trigger_html = ""
    if triggers:
        trigger_html = "<h3 style='color:#c62828'>Actieve signalen</h3>"
        iconen = {"earnings": "Earnings", "concentratie": "Concentratie",
                  "koopzone": "Koopzone", "dca": "DCA", "fiscaal": "Fiscaal"}
        for t in sorted(triggers, key=lambda x: not x.get("urgent")):
            kleur = "#b71c1c" if t.get("urgent") else "#e65100"
            bg    = "#ffebee" if t.get("urgent") else "#fff3e0"
            tag   = iconen.get(t["type"], t["type"])
            trigger_html += (
                f"<div style='background:{bg};border-left:4px solid {kleur};"
                f"padding:10px 14px;margin:6px 0;border-radius:0 6px 6px 0'>"
                f"<strong style='color:{kleur}'>[{tag}] {t['label']}</strong></div>"
            )

    # Stale data waarschuwing
    stale_syms = [h["name"] for h in holdings if h.get("stale")]
    stale_html = ""
    if stale_syms:
        stale_html = (
            "<div style='background:#fff8e1;border-left:4px solid #ffa000;"
            "padding:8px 12px;margin:8px 0'>"
            f"<strong>Waarschuwing: geen actuele koers beschikbaar voor "
            f"{', '.join(stale_syms)} — aankoopprijs getoond als huidige koers.</strong></div>"
        )

    # Earnings kalender
    naam_map = {h["ticker"]: h["name"] for h in holdings}
    naam_map.update({t: info["name"] for t, info in WATCHLIST.items()})
    alle_tickers = [h["ticker"] for h in holdings] + list(WATCHLIST.keys())
    earnings_rijen = ""
    for ticker in alle_tickers:
        md   = market.get(ticker, {})
        ed   = md.get("earnings_date")
        fmt  = md.get("earnings_fmt", "onbekend")
        naam = naam_map.get(ticker, ticker)
        if ed and isinstance(ed, date):
            dagen = (ed - date.today()).days
            bg    = "#ffebee" if 0 <= dagen <= EARNINGS_HORIZON else ("#fff8e1" if dagen <= 30 else "")
            tag   = f"<strong style='color:#c62828'>{dagen}d</strong>" if 0 <= dagen <= EARNINGS_HORIZON else f"{dagen}d"
            earnings_rijen += f"<tr style='background:{bg}'><td>{naam}</td><td>{ticker}</td><td>{fmt}</td><td>{tag}</td></tr>"
        else:
            earnings_rijen += f"<tr><td>{naam}</td><td>{ticker}</td><td style='color:#999'>onbekend</td><td>—</td></tr>"

    # Posities (anti-anchoring: geen aankoopprijs)
    positie_rijen = ""
    for h in sorted(holdings, key=lambda x: x["eur_val"], reverse=True):
        sl        = round(h["cost"] * (1 - STOP_LOSS_PCT / 100), 2)
        sl_buf    = round((h["price"] - sl) * h["qty"], 2)
        sl_pct    = round((h["price"] / sl - 1) * 100, 1) if sl else 0
        pct       = round(h["eur_val"] / sat_val_eur * 100, 1) if sat_val_eur else 0
        bg        = "#ffebee" if pct > CONCENTRATION_MAX else ("#fff8e1" if pct > 28 else "")
        stale_tag = " <small style='color:#e65100'>[stale]</small>" if h.get("stale") else ""
        positie_rijen += (
            f"<tr style='background:{bg}'>"
            f"<td><strong>{h['name']}</strong>{stale_tag}<br>"
            f"<small style='color:#666'>{h['symbol']} · {h['sector']}</small></td>"
            f"<td align='right'>{h['qty']:.0f}</td>"
            f"<td align='right'><strong>{h['price']:.2f} {h['currency']}</strong></td>"
            f"<td align='right'>{h['value']:,.2f} {h['currency']}</td>"
            f"<td align='right'>{pct}%</td>"
            f"<td align='right' style='font-size:12px'>Buffer {h['currency']} {sl_buf:,.0f}"
            f"<br><small style='color:#666'>({sl_pct:+.1f}% tot stop)</small></td>"
            f"</tr>"
        )

    # Sectorspreiding
    sectoren = {}
    for h in holdings:
        sectoren[h["sector"]] = sectoren.get(h["sector"], 0) + h["eur_val"]
    sector_kleuren = {
        "Healthcare": "#e53935", "Financials": "#1e88e5",
        "Technology": "#43a047", "Consumer Staples": "#fb8c00",
        "Industrials": "#8e24aa",
    }
    sector_bars = ""
    for sector, val in sorted(sectoren.items(), key=lambda x: x[1], reverse=True):
        pct   = round(val / sat_val_eur * 100, 1) if sat_val_eur else 0
        kleur = sector_kleuren.get(sector, "#90a4ae")
        warn  = " !" if pct > 40 else ""
        sector_bars += (
            f"<tr><td style='width:140px;font-size:13px'>{sector}{warn}</td>"
            f"<td><div style='background:{kleur};width:{int(pct*2)}px;height:18px;"
            f"border-radius:3px;display:inline-block'></div>"
            f" <span style='font-size:13px'>{pct}%</span></td></tr>"
        )
    for sector in ["Consumer Staples", "Industrials"]:
        if sector not in sectoren:
            sector_bars += (
                f"<tr><td style='color:#999;font-size:13px'>{sector} (ontbreekt)</td>"
                f"<td><span style='font-size:12px;color:#999'>0% - volgende aankoop</span></td></tr>"
            )

    # Watchlist
    wl_rijen = ""
    for ticker, info in WATCHLIST.items():
        md        = market.get(ticker, {})
        prijs     = md.get("price")
        fpe       = md.get("forward_pe")
        hist      = info.get("hist_pe") or HIST_PE.get(ticker)
        prijs_str = f"{prijs:.2f}" if prijs else "—"
        fpe_str   = f"{fpe:.1f}x" if fpe else "—"
        hist_str  = f"{hist:.1f}x" if hist else "—"
        if fpe and hist:
            delta  = round((fpe / hist - 1) * 100, 1)
            status = (f"<strong style='color:#2e7d32'>KOOPZONE ({delta:+.1f}%)</strong>"
                      if fpe < hist else f"{delta:+.1f}% boven gem.")
        else:
            status = "<span style='color:#999'>geen data</span>"
        wl_rijen += (
            f"<tr><td>#{info['prio']} {info['name']}<br>"
            f"<small style='color:#666'>{ticker} · {info['sector']}</small></td>"
            f"<td align='right'>{prijs_str}</td>"
            f"<td align='right'>{fpe_str}</td>"
            f"<td align='right'>{hist_str}</td>"
            f"<td>{status}</td></tr>"
        )

    # Fiscale progress bar
    bar_w = min(int(vrijstelling_pct), 100)
    bar_color = "#43a047" if vrijstelling_pct < 80 else "#ffa000" if vrijstelling_pct < 95 else "#f44336"

    html = f"""<!DOCTYPE html>
<html><body style="font-family:Arial,sans-serif;max-width:680px;margin:auto;color:#333;font-size:14px">

<h2 style="color:#1565c0;margin-bottom:4px">Portfolio Monitor &mdash; {today}</h2>
<p style="color:#888;font-size:12px;margin-top:0">Trigger-gebaseerd &middot; Saxo Bank + Yahoo Finance</p>

<table width="100%" cellpadding="10" style="background:#e3f2fd;border-radius:8px;margin-bottom:16px;border-collapse:collapse">
<tr>
  <td><div style="font-size:11px;color:#666">TOTAAL BELEGD</div>
      <div style="font-size:22px;font-weight:bold;color:#1565c0">&euro;{totaal:,.2f}</div></td>
  <td><div style="font-size:11px;color:#666">ETF KERN (Bolero)</div>
      <div style="font-size:16px">&euro;{etf_kern:,.2f} <span style="color:#2e7d32">({etf_pct}%)</span></div></td>
  <td><div style="font-size:11px;color:#666">SATELLIET (Saxo)</div>
      <div style="font-size:16px">&euro;{sat_val_eur:,.2f} <span style="color:#1565c0">({sat_pct}%)</span></div></td>
  <td><div style="font-size:11px;color:#666">DCA COUNTDOWN</div>
      <div style="font-size:16px;{'color:#c62828;font-weight:bold' if dagen_dca <= DCA_ALARM_DAGEN else ''}">{dagen_dca}d</div>
      <div style="font-size:11px;color:#888">{next_dca.strftime('%d/%m/%Y')}</div></td>
</tr>
</table>

{stale_html}
{trigger_html}

<h3 style="border-bottom:2px solid #e3f2fd;padding-bottom:4px">Earnings Kalender</h3>
<table width="100%" cellpadding="6" cellspacing="0" style="border-collapse:collapse;font-size:13px">
<tr style="background:#f5f5f5"><th align="left">Bedrijf</th><th align="left">Ticker</th><th align="left">Datum</th><th align="left">Afstand</th></tr>
{earnings_rijen}
</table>

<h3 style="border-bottom:2px solid #e3f2fd;padding-bottom:4px;margin-top:20px">Posities</h3>
<p style="font-size:11px;color:#999;margin:0 0 6px">Aankoopprijs verborgen (anchoring-preventie). Buffer = ruimte tot Zanna -30% stop-loss.</p>
<table width="100%" cellpadding="7" cellspacing="0" style="border-collapse:collapse;font-size:13px">
<tr style="background:#f5f5f5">
  <th align="left">Positie</th><th align="right">Qty</th><th align="right">Koers</th>
  <th align="right">Waarde</th><th align="right">% Sat.</th><th align="right">Stop-buffer</th>
</tr>
{positie_rijen}
</table>

<h3 style="border-bottom:2px solid #e3f2fd;padding-bottom:4px;margin-top:20px">Sectorspreiding Satelliet</h3>
<table cellpadding="4" cellspacing="0">{sector_bars}</table>

<h3 style="border-bottom:2px solid #e3f2fd;padding-bottom:4px;margin-top:20px">Watchlist &mdash; 90-sec check</h3>
<table width="100%" cellpadding="7" cellspacing="0" style="border-collapse:collapse;font-size:13px">
<tr style="background:#f5f5f5"><th align="left">Bedrijf</th><th align="right">Koers</th>
  <th align="right">Fwd P/E</th><th align="right">Hist. gem.</th><th align="left">Status</th></tr>
{wl_rijen}
</table>

<h3 style="border-bottom:2px solid #e3f2fd;padding-bottom:4px;margin-top:20px">Fiscale Tracker 2026</h3>
<table width="100%" cellpadding="8" cellspacing="0" style="border-collapse:collapse;font-size:13px">
<tr>
  <td style="background:#f5f5f5;width:34%">
    <div style="font-size:11px;color:#666">MEERWAARDE YTD</div>
    <div style="font-size:18px;font-weight:bold">&euro;{meerwaarde:,.2f}</div>
    <div style="font-size:11px;color:#888">/ &euro;10.000 vrijstelling ({vrijstelling_pct}%)</div>
    <div style="background:#e0e0e0;height:6px;border-radius:3px;margin-top:4px">
      <div style="background:{bar_color};height:6px;border-radius:3px;width:{bar_w}%"></div>
    </div>
  </td>
  <td style="background:#f5f5f5;width:33%;padding-left:14px">
    <div style="font-size:11px;color:#666">TOB BETAALD YTD</div>
    <div style="font-size:18px;font-weight:bold">&euro;{tob_ytd:,.2f}</div>
  </td>
  <td style="background:#f5f5f5;width:33%;padding-left:14px">
    <div style="font-size:11px;color:#666">DIVIDENDEN YTD</div>
    <div style="font-size:18px;font-weight:bold">&euro;{dividenden:,.2f}</div>
    <div style="font-size:11px;color:#888">/ &euro;800 terugvorderbaar</div>
  </td>
</tr>
</table>

<h3 style="border-bottom:2px solid #e3f2fd;padding-bottom:4px;margin-top:20px">Performance Sinds Start</h3>
<table width="100%" cellpadding="8" cellspacing="0" style="border-collapse:collapse;font-size:13px">
<tr>
  <td style="background:#f5f5f5;width:50%">
    <div style="font-size:11px;color:#666">ETF KERN &mdash; ACWI IMI (sinds 27/02/2026)</div>
    <div style="font-size:20px;font-weight:bold;color:#2e7d32">{etf_return:+.2f}%</div>
  </td>
  <td style="background:#f5f5f5;width:50%;padding-left:14px">
    <div style="font-size:11px;color:#666">SATELLIET (sinds 02/04/2026)</div>
    <div style="font-size:20px;font-weight:bold;color:{'#2e7d32' if sat_return >= 0 else '#c62828'}">{sat_return:+.2f}%</div>
  </td>
</tr>
</table>
<p style="font-size:11px;color:#999;font-style:italic">Vergelijk ETF vs. satelliet pas na 3+ jaar (PP1). Kortetermijn underperformance is normaal.</p>

<hr style="margin:20px 0;border:none;border-top:1px solid #e0e0e0">
<p style="color:#bbb;font-size:11px">Portfolio Monitor v2 &middot; {date.today()} &middot; Saxo Bank + Yahoo Finance &middot; PP1&ndash;PP3 + KB1&ndash;KB10</p>
</body></html>"""

    return subject, html


# ── Email versturen ───────────────────────────────────────────────────────────
def send_email(subject: str, html: str) -> bool:
    password = GMAIL_APP_PASSWORD.replace(" ", "")
    if not password:
        print("FOUT: GMAIL_APP_PASSWORD niet ingesteld.")
        return False
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"]    = GMAIL_USER
    msg["To"]      = GMAIL_USER
    msg.set_content("Open in HTML viewer voor opmaak.")
    msg.add_alternative(html, subtype="html")
    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as s:
            s.login(GMAIL_USER, password)
            s.send_message(msg)
        print(f"Email verstuurd: {subject}")
        return True
    except Exception as e:
        print(f"Email mislukt: {e}")
        return False


# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    force = "--force" in sys.argv
    print(f"=== Portfolio Monitor v2 — {date.today()} {'(force)' if force else ''} ===")

    print("1. ETF waarde ophalen...")
    etf_kern = get_etf_value()
    print(f"   IMIE.MI: {ETF_SHARES} aandelen x ... = EUR {etf_kern:,.2f}")

    print("2. Saxo portfolio ophalen...")
    raw      = get_portfolio()
    holdings = raw["holdings"]
    sat_val_eur = sum(h["eur_val"] for h in holdings)
    print(f"   {len(holdings)} posities | Saxo totaal EUR {raw['total_value']:,.2f}")

    print("3. Marktdata ophalen (P/E + earnings)...")
    market = {}
    alle_tickers = [h["ticker"] for h in holdings] + list(WATCHLIST.keys())
    for ticker in alle_tickers:
        val = _yf_valuation(ticker)
        val["price"] = _yf_price(ticker)
        market[ticker] = val
        print(f"   {ticker}: P/E={val.get('forward_pe')}, Earnings={val.get('earnings_fmt')}")

    print("4. Dividenden + fiscale data + history...")
    # Dividenden automatisch bijwerken via Yahoo Finance
    try:
        from fetch_dividends import fetch_dividends_since, update_fiscal as _update_div_fiscal
        alle_divs = []
        for sym, cfg in POSITIONS.items():
            from config import AGENTS_DIR as _AD
            aankoop_map = {
                "UCB:xbru": "2026-04-20", "HLNE:xnas": "2026-04-17",
                "AOF:xetr":  "2026-04-14", "V:xnys":    "2026-04-02",
            }
            divs = fetch_dividends_since(cfg["ticker"],
                                         aankoop_map.get(sym, "2026-01-01"),
                                         cfg["qty"])
            for d in divs:
                d["ticker"] = cfg["ticker"]; d["symbol"] = sym
                d["currency"] = cfg["currency"]
                alle_divs.append(d)
        if alle_divs:
            nieuw, totaal_div = _update_div_fiscal(alle_divs)
            if nieuw:
                print(f"   {nieuw} nieuwe dividenden gevonden, totaal EUR {totaal_div:.2f}")
    except Exception as e:
        print(f"   Dividenden fout: {e}")

    fiscal  = load_fiscal()
    totaal  = sat_val_eur + etf_kern
    history = update_history(totaal)

    print("5. Triggers evalueren...")
    triggers = check_triggers(holdings, market, fiscal, sat_val_eur)
    for t in triggers:
        print(f"   {'[!]' if t.get('urgent') else '[*]'} [{t['type']}] {t['label']}")

    if not should_send(triggers) and not force:
        print("Geen triggers en niet maandag — geen email.")
        return

    print("6. Email bouwen en versturen...")
    subject, html = build_html(holdings, market, fiscal, triggers, history, etf_kern)
    path = os.path.join(REPORTS_DIR, f"monitor_{date.today()}.html")
    with open(path, "w", encoding="utf-8") as f:
        f.write(html)
    send_email(subject, html)
    print(f"Klaar. Rapport: {path}")


if __name__ == "__main__":
    main()
