"""
Cloud Portfolio Monitor — draait op GitHub Actions, geen lokale computer nodig.
Gebruikt Yahoo Finance (geen Saxo auth vereist).

Timing: stuurt email ALLEEN bij actief earnings/thesis signaal of trigger.
Geen dagelijkse spam — enkel wanneer er iets concreets te doen is.
"""
import json, os, smtplib, urllib.request, urllib.parse
from datetime import date, datetime
from email.message import EmailMessage
from typing import Optional

from config import (
    GMAIL_USER, GMAIL_APP_PASSWORD,
    BASE_DIR,
    ETF_TICKER, ETF_SHARES, ETF_INVESTED, SAT_INVESTED,
    POSITIONS, WATCHLIST, HIST_PE, EURUSD,
    STOP_LOSS_PCT, CONCENTRATION_MAX, DCA_ALARM_DAGEN,
    next_dca_date, POSITIONS_FILE, THESIS_CHECKS,
)


def _load_tracker_portfolio() -> dict:
    """Lees dezelfde posities als de lokale Portfolio Tracker, als die beschikbaar is."""
    path = os.path.join(BASE_DIR, "Porfolio tracker", "build", "positions.json")
    if not os.path.exists(path):
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return {}

    holdings = data.get("holdings", [])
    etf = next((h for h in holdings if h.get("type") == "ETF"), None)
    satellites = [h for h in holdings if h.get("type") != "ETF"]

    def shares(h):
        return sum(float(l.get("shares", 0)) for l in h.get("lots", []))

    def invested(h):
        return sum(float(l.get("shares", 0)) * float(l.get("price", 0)) for l in h.get("lots", []))

    return {
        "etf": {
            "ticker": etf.get("yahoo", etf.get("ticker")) if etf else ETF_TICKER,
            "shares": shares(etf) if etf else ETF_SHARES,
            "invested": invested(etf) if etf else ETF_INVESTED,
        } if etf else None,
        "positions": {
            f"{h.get('ticker')}:tracker": {
                "ticker": h.get("yahoo", h.get("ticker")),
                "cost": invested(h) / shares(h) if shares(h) else 0,
                "qty": shares(h),
                "currency": h.get("currency", "EUR"),
                "name": h.get("name", h.get("ticker")),
                "sector": h.get("sector", "Overig"),
            }
            for h in satellites
        },
        "sat_invested": sum(invested(h) for h in satellites),
    }


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


EARNINGS_CONFIG_FILE = os.path.join(os.path.dirname(__file__), "earnings_config.json")

def _yf_forward_pe(ticker: str) -> Optional[float]:
    """Forward P/E via timeseries — werkt voor US én Europese aandelen."""
    enc = urllib.parse.quote(ticker)
    url = (f"https://query1.finance.yahoo.com/ws/fundamentals-timeseries/v1/finance/"
           f"timeseries/{enc}?type=trailingForwardPeRatio,annualForwardPeRatio"
           f"&period1=1700000000&period2=1800000000")
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(req, timeout=8) as r:
            d = json.loads(r.read())
        for result in d.get("timeseries", {}).get("result", []):
            vals = result.get("trailingForwardPeRatio") or result.get("annualForwardPeRatio") or []
            if vals:
                last = vals[-1]
                raw  = (last.get("reportedValue", {}) if isinstance(last, dict) else {})
                pe   = raw.get("raw") if isinstance(raw, dict) else raw
                if pe:
                    return round(float(pe), 2)
    except Exception:
        pass
    return None


def _eurusd_rate() -> float:
    """Live EURUSD; fallback naar configwaarde als Yahoo faalt."""
    rate = _yf_price("EURUSD=X")
    return float(rate) if rate else EURUSD

def _get_earnings_config() -> dict:
    if not os.path.exists(EARNINGS_CONFIG_FILE):
        return {}
    with open(EARNINGS_CONFIG_FILE, encoding="utf-8") as f:
        return json.load(f).get("earnings", {})


def _yf_next_earnings(ticker: str) -> Optional[str]:
    """Auto-detectie volgende earnings via Yahoo calendarEvents."""
    enc = urllib.parse.quote(ticker)
    url = (f"https://query1.finance.yahoo.com/v10/finance/quoteSummary/{enc}"
           f"?modules=calendarEvents")
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(req, timeout=8) as r:
            d = json.loads(r.read())
        events = d["quoteSummary"]["result"][0]["calendarEvents"]
        dates  = events.get("earnings", {}).get("earningsDate", [])
        if dates:
            ts = dates[0].get("raw", 0)
            return datetime.fromtimestamp(ts).strftime("%Y-%m-%d")
    except Exception:
        pass
    return None


def _check_stale_earnings(earnings_cfg: dict) -> list:
    """Geeft lijst van tickers waarvan de earnings-datum al gepasseerd is (>7 dagen)."""
    today  = date.today()
    stale  = []
    for ticker, cfg in earnings_cfg.items():
        d_str = cfg.get("date")
        if not d_str:
            continue
        d = datetime.strptime(d_str, "%Y-%m-%d").date()
        if (today - d).days > 7:
            auto = _yf_next_earnings(ticker)
            stale.append({"ticker": ticker, "old_date": d_str,
                          "label": cfg.get("label", ""), "auto_next": auto})
    return stale


def should_send(earnings_cfg: dict, triggers: list) -> bool:
    """Stuurt email ALLEEN bij earnings binnen 14 dagen of actieve trigger."""
    today = date.today()
    for cfg in earnings_cfg.values():
        d_str = cfg.get("date")
        if not d_str:
            continue
        d     = datetime.strptime(d_str, "%Y-%m-%d").date()
        dagen = (d - today).days
        if 0 <= dagen <= 14:
            return True
    return len(triggers) > 0


def build_and_send():
    today     = date.today()
    next_dca  = next_dca_date()
    dagen_dca = (next_dca - today).days

    earnings_cfg = _get_earnings_config()
    tracker_portfolio = _load_tracker_portfolio()
    etf_cfg = tracker_portfolio.get("etf") or {
        "ticker": ETF_TICKER,
        "shares": ETF_SHARES,
        "invested": ETF_INVESTED,
    }
    positions = tracker_portfolio.get("positions") or POSITIONS
    sat_invested = tracker_portfolio.get("sat_invested") or SAT_INVESTED
    eurusd = _eurusd_rate()
    portfolio_source = "tracker positions.json" if tracker_portfolio.get("etf") else "agents/config.py"
    print(
        "Portfolio bron: "
        f"{portfolio_source}; ETF {etf_cfg['ticker']} shares={etf_cfg['shares']} "
        f"invested={etf_cfg['invested']:.2f}; EURUSD={eurusd:.4f}"
    )

    # ETF waarde
    etf_price = _yf_price(etf_cfg["ticker"])
    etf_kern  = round((etf_price or etf_cfg["invested"] / etf_cfg["shares"]) * etf_cfg["shares"], 2)

    # Posities ophalen via Yahoo Finance
    holdings = []
    for sym, cfg in positions.items():
        ticker  = cfg["ticker"]
        price   = _yf_price(ticker)
        stale   = price is None
        price   = price or cfg["cost"]
        value   = round(price * cfg["qty"], 2)
        eur_val = value if cfg["currency"] == "EUR" else round(value / eurusd, 2)
        sl      = round(cfg["cost"] * (1 - STOP_LOSS_PCT / 100), 2)
        sl_buf  = round((price - sl) * cfg["qty"], 2)
        holdings.append({**cfg, "symbol": sym, "price": price, "value": value,
                         "eur_val": eur_val, "sl": sl, "sl_buf": sl_buf, "stale": stale})

    sat_val_eur = sum(h["eur_val"] for h in holdings)
    totaal      = sat_val_eur + etf_kern
    etf_pct     = round(etf_kern / totaal * 100, 1)
    sat_pct     = round(sat_val_eur / totaal * 100, 1)
    etf_return  = round((etf_kern - etf_cfg["invested"]) / etf_cfg["invested"] * 100, 2)
    sat_return  = round((sat_val_eur - sat_invested) / sat_invested * 100, 2)

    # Triggers
    triggers = []

    # Earnings triggers — 14-dagenvenster voor thesis-check
    for sym, cfg_vals in positions.items():
        earn_cfg  = earnings_cfg.get(cfg_vals["ticker"], {})
        d_str     = earn_cfg.get("date")
        if d_str:
            ed    = datetime.strptime(d_str, "%Y-%m-%d").date()
            dagen = (ed - today).days
            if 0 <= dagen <= 14:
                triggers.insert(0, f"Earnings {cfg_vals['name']} over {dagen} dagen ({ed.strftime('%d/%m/%Y')})")

    # Watchlist
    watchlist_data = []
    for ticker, info in WATCHLIST.items():
        price = _yf_price(ticker)
        fpe   = _yf_forward_pe(ticker)
        hist  = info.get("hist_pe") or HIST_PE.get(ticker)
        status = ""
        if fpe and hist:
            delta  = round((fpe / hist - 1) * 100, 1)
            status = f"KOOPZONE ({delta:+.1f}%)" if fpe < hist else f"{delta:+.1f}% boven gem."
        watchlist_data.append({**info, "ticker": ticker, "price": price, "fpe": fpe,
                                "hist": hist, "status": status})

    # Overige triggers
    for h in holdings:
        pct = round(h["eur_val"] / sat_val_eur * 100, 1) if sat_val_eur else 0
        if pct > CONCENTRATION_MAX:
            triggers.append(f"Concentratie: {h['name']} is {pct}% van satelliet (drempel {CONCENTRATION_MAX}%)")
    if dagen_dca <= DCA_ALARM_DAGEN:
        triggers.append(f"DCA nadert: nog {dagen_dca} dagen ({next_dca.strftime('%d/%m/%Y')})")
    for w in watchlist_data:
        if w.get("fpe") and w.get("hist") and w["fpe"] < w["hist"]:
            triggers.append(f"Koopzone: {w['name']} Fwd P/E {w['fpe']:.1f}x < hist. {w['hist']}x")

    # Stale earnings detecteren
    stale_earnings = _check_stale_earnings(earnings_cfg)

    # Timing-filter: stuur ALLEEN als er iets concreet te doen is
    if not should_send(earnings_cfg, triggers) and not stale_earnings:
        print(f"Geen earnings binnen 14 dagen en geen triggers — geen email vandaag ({today}).")
        return

    # Thesis check blok voor earnings <= 14 dagen
    thesis_blok = ""
    naam_map = {cfg["ticker"]: cfg["name"] for cfg in positions.values()}
    for sym, cfg_vals in positions.items():
        earn_cfg_item = earnings_cfg.get(cfg_vals["ticker"], {})
        d_str = earn_cfg_item.get("date")
        if not d_str:
            continue
        ed    = datetime.strptime(d_str, "%Y-%m-%d").date()
        dagen = (ed - today).days
        if 0 <= dagen <= 14:
            checks = THESIS_CHECKS.get(sym, [])
            if checks:
                items = "".join(f"<li style='margin:3px 0'>{c}</li>" for c in checks)
                label = earn_cfg_item.get("label", "")
                thesis_blok += (
                    f"<div style='background:#e8f5e9;border-left:4px solid #2e7d32;"
                    f"padding:10px 14px;margin:8px 0;border-radius:0 6px 6px 0'>"
                    f"<strong>Thesis-check {cfg_vals['name']}</strong>"
                    f" &mdash; {ed.strftime('%d/%m/%Y')} ({dagen} dagen)"
                    f"<small style='color:#666;margin-left:8px'>{label}</small>"
                    f"<ul style='margin:6px 0;padding-left:18px;font-size:13px'>{items}</ul>"
                    f"</div>"
                )

    # Stale earnings HTML
    stale_earn_html = ""
    if stale_earnings:
        items = ""
        for s in stale_earnings:
            auto_str = (f" &rarr; Yahoo schat: <strong>{s['auto_next']}</strong>"
                        if s["auto_next"] else " &rarr; Yahoo kon datum niet ophalen")
            items += (f"<li><strong>{s['ticker']}</strong> {s['label']} "
                      f"was {s['old_date']}{auto_str}"
                      f" — update <code>agents/earnings_config.json</code></li>")
        stale_earn_html = (
            f"<div style='background:#fff3e0;border-left:4px solid #e65100;"
            f"padding:10px 14px;margin:8px 0;border-radius:0 6px 6px 0'>"
            f"<strong>Earnings data verouderd — update vereist:</strong>"
            f"<ul style='margin:6px 0;padding-left:18px;font-size:13px'>{items}</ul>"
            f"</div>"
        )

    # Email HTML
    stale_warn = ""
    stale_syms = [h["name"] for h in holdings if h.get("stale")]
    if stale_syms:
        stale_warn = (f"<div style='background:#fff8e1;border-left:4px solid #ffa000;padding:8px 12px;margin:8px 0'>"
                      f"<strong>Waarschuwing:</strong> geen actuele koers voor {', '.join(stale_syms)} "
                      f"&mdash; aankoopprijs getoond.</div>")

    trigger_html = ""
    if triggers:
        trigger_html = "<h3 style='color:#c62828'>Actieve signalen</h3>"
        for t in triggers:
            trigger_html += (f"<div style='background:#fff3e0;border-left:4px solid #e65100;"
                             f"padding:10px;margin:4px 0;border-radius:0 6px 6px 0'>{t}</div>")

    positie_rijen = ""
    for h in sorted(holdings, key=lambda x: x["eur_val"], reverse=True):
        pct = round(h["eur_val"] / sat_val_eur * 100, 1) if sat_val_eur else 0
        bg  = "#ffebee" if pct > CONCENTRATION_MAX else ("#fff8e1" if pct > 28 else "")
        stale_tag = " <small style='color:#e65100'>[stale]</small>" if h.get("stale") else ""
        positie_rijen += (
            f"<tr style='background:{bg}'>"
            f"<td><strong>{h['name']}</strong>{stale_tag}<br>"
            f"<small style='color:#666'>{h['ticker']} &middot; {h['sector']}</small></td>"
            f"<td align='right'>{h['qty']}</td>"
            f"<td align='right'><strong>{h['price']:.2f} {h['currency']}</strong></td>"
            f"<td align='right'>{h['value']:,.2f} {h['currency']}</td>"
            f"<td align='right'>{pct}%</td>"
            f"<td align='right' style='font-size:12px'>{h['currency']} {h['sl_buf']:,.0f}"
            f"<br><small style='color:#666'>(tot stop -30%)</small></td></tr>"
        )

    wl_rijen = ""
    for w in watchlist_data:
        fpe_str   = f"{w['fpe']:.1f}x" if w.get("fpe") else "&mdash;"
        hist_str  = f"{w['hist']:.1f}x" if w.get("hist") else "&mdash;"
        prijs_str = f"{w['price']:.2f}" if w.get("price") else "&mdash;"
        kleur = "color:#2e7d32;font-weight:bold" if "KOOPZONE" in w.get("status", "") else ""
        wl_rijen += (f"<tr><td>#{w['prio']} {w['name']} ({w['ticker']})</td>"
                     f"<td align='right'>{prijs_str}</td>"
                     f"<td align='right'>{fpe_str}</td>"
                     f"<td align='right'>{hist_str}</td>"
                     f"<td style='{kleur}'>{w.get('status') or '&mdash;'}</td></tr>")

    sector_kleuren = {"Healthcare": "#e53935", "Financials": "#1e88e5",
                      "Technology": "#43a047", "Consumer Staples": "#fb8c00", "Industrials": "#8e24aa"}
    sectoren = {}
    for h in holdings:
        sectoren[h["sector"]] = sectoren.get(h["sector"], 0) + h["eur_val"]
    sector_bars = ""
    for sector, val in sorted(sectoren.items(), key=lambda x: x[1], reverse=True):
        pct   = round(val / sat_val_eur * 100, 1) if sat_val_eur else 0
        kleur = sector_kleuren.get(sector, "#90a4ae")
        sector_bars += (f"<tr><td style='width:140px;font-size:13px'>{sector}</td>"
                        f"<td><div style='background:{kleur};width:{int(pct*2)}px;height:18px;"
                        f"border-radius:3px;display:inline-block'></div>"
                        f" <span style='font-size:13px'>{pct}%</span></td></tr>")
    for s in ["Consumer Staples", "Industrials"]:
        if s not in sectoren:
            sector_bars += (f"<tr><td style='color:#999;font-size:13px'>{s} (ontbreekt)</td>"
                            f"<td><span style='font-size:12px;color:#999'>0% &rarr; volgende aankoop</span></td></tr>")

    html = f"""<!DOCTYPE html><html><body style="font-family:Arial,sans-serif;max-width:680px;margin:auto;color:#333;font-size:14px">
<h2 style="color:#1565c0">Portfolio Slotkoersen &mdash; {today.strftime('%d/%m/%Y')}</h2>
<p style="color:#888;font-size:12px">Cloud agent (GitHub Actions) &middot; na sluiting NYSE/NASDAQ 22:15 CET</p>

<table width="100%" cellpadding="10" style="background:#e3f2fd;border-radius:8px;margin-bottom:16px;border-collapse:collapse">
<tr>
  <td><div style="font-size:11px;color:#666">TOTAAL</div>
      <div style="font-size:20px;font-weight:bold;color:#1565c0">&euro;{totaal:,.2f}</div></td>
  <td><div style="font-size:11px;color:#666">ETF KERN</div>
      <div style="font-size:15px">&euro;{etf_kern:,.2f} ({etf_pct}%)</div></td>
  <td><div style="font-size:11px;color:#666">SATELLIET</div>
      <div style="font-size:15px">&euro;{sat_val_eur:,.2f} ({sat_pct}%)</div></td>
  <td><div style="font-size:11px;color:#666">DCA</div>
      <div style="font-size:15px;{'color:#c62828;font-weight:bold' if dagen_dca<=DCA_ALARM_DAGEN else ''}">{dagen_dca}d</div>
      <div style="font-size:11px;color:#888">{next_dca.strftime('%d/%m/%Y')}</div></td>
</tr>
</table>

{stale_warn}{trigger_html}
{thesis_blok}
{stale_earn_html}

<h3 style="border-bottom:2px solid #e3f2fd;padding-bottom:4px">Posities (slotkoersen)</h3>
<p style="font-size:11px;color:#999;margin:0 0 6px">Aankoopprijs verborgen. Buffer = ruimte tot Zanna -30% stop-loss.</p>
<table width="100%" cellpadding="6" cellspacing="0" style="border-collapse:collapse;font-size:13px">
<tr style="background:#f5f5f5"><th align="left">Positie</th><th align="right">Qty</th>
  <th align="right">Slotkoers</th><th align="right">Waarde</th><th align="right">% Sat.</th><th align="right">Stop-buffer</th></tr>
{positie_rijen}
</table>

<h3 style="border-bottom:2px solid #e3f2fd;padding-bottom:4px;margin-top:20px">Sectorspreiding Satelliet</h3>
<table cellpadding="4" cellspacing="0">{sector_bars}</table>

<h3 style="border-bottom:2px solid #e3f2fd;padding-bottom:4px;margin-top:20px">Watchlist &mdash; 90-sec check</h3>
<table width="100%" cellpadding="6" cellspacing="0" style="border-collapse:collapse;font-size:13px">
<tr style="background:#f5f5f5"><th align="left">Bedrijf</th><th align="right">Koers</th>
  <th align="right">Fwd P/E</th><th align="right">Hist. gem.</th><th align="left">Status</th></tr>
{wl_rijen}
</table>

<h3 style="border-bottom:2px solid #e3f2fd;padding-bottom:4px;margin-top:20px">Performance Sinds Start</h3>
<table width="100%" cellpadding="8" style="border-collapse:collapse;font-size:13px">
<tr>
  <td style="background:#f5f5f5;width:50%">
    <div style="font-size:11px;color:#666">ETF KERN &mdash; ACWI IMI (sinds 27/02/2026)</div>
    <div style="font-size:20px;font-weight:bold;color:#2e7d32">{etf_return:+.2f}%</div>
  </td>
  <td style="background:#f5f5f5;width:50%;padding-left:12px">
    <div style="font-size:11px;color:#666">SATELLIET (sinds 02/04/2026)</div>
    <div style="font-size:20px;font-weight:bold;color:{'#2e7d32' if sat_return>=0 else '#c62828'}">{sat_return:+.2f}%</div>
  </td>
</tr>
</table>
<p style="font-size:11px;color:#999;font-style:italic">Vergelijk pas na 3+ jaar (PP1). Kortetermijn underperformance is normaal.</p>

<hr style="margin:16px 0;border:none;border-top:1px solid #e0e0e0">
<p style="color:#bbb;font-size:11px">Cloud Portfolio Monitor &middot; GitHub Actions &middot; {today}</p>
</body></html>"""

    earnings_soon = [t for t in triggers if "Earnings" in t]
    if earnings_soon:
        subject = f"[THESIS] {today.strftime('%d/%m/%Y')} — {earnings_soon[0][:55]}"
    elif stale_earnings:
        subject = f"[UPDATE] Earnings data verouderd — {', '.join(s['ticker'] for s in stale_earnings)}"
    elif triggers:
        subject = f"[SIGNAAL] Portfolio {today.strftime('%d/%m/%Y')} — {len(triggers)} signaal(en)"
    else:
        subject = f"Portfolio Monitor {today.strftime('%d/%m/%Y')}"

    password = GMAIL_APP_PASSWORD.replace(" ", "")
    if not password:
        print("FOUT: GMAIL_APP_PASSWORD niet ingesteld.")
        return
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
