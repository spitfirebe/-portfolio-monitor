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
    FISCAL_ALARM_PCT, next_dca_date, THESIS_CHECKS, CAGR_AANNAMES, EGM_DREMPEL,
)
def _get_portfolio_offline() -> dict:
    """Yahoo Finance + statische config — geen Saxo login vereist."""
    holdings = []
    for symbol, cfg in POSITIONS.items():
        ticker  = cfg["ticker"]
        qty     = cfg["qty"]
        cost    = cfg["cost"]
        curr    = cfg["currency"]
        price   = _yf_price(ticker)
        stale   = price is None
        price   = price or cost
        eur_val = (price * qty) if curr == "EUR" else round(price * qty / EURUSD, 2)
        holdings.append({
            "symbol":   symbol,
            "name":     cfg["name"],
            "sector":   cfg["sector"],
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
    total_eur = sum(h["eur_val"] for h in holdings)
    return {"account_id": "offline", "total_value": total_eur, "cash": 0, "holdings": holdings}

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


# ── EGM berekening ───────────────────────────────────────────────────────────
def bereken_egm(fwd_pe: float, hist_pe: float,
                eps_growth: float, div_yield: float, horizon: int = 5) -> float:
    """
    Slegers Earnings Growth Model — verwacht jaarrendement over 5 jaar.
    EGM = EPS groei + dividend yield + jaarlijkse P/E mean reversion
    KOOPZONE is alleen valide als EGM >= EGM_DREMPEL (10%).
    """
    pe_reversion = ((hist_pe / fwd_pe) ** (1 / horizon) - 1) * 100
    return round(eps_growth + div_yield + pe_reversion, 1)


# ── Hoofdactie bepalen ────────────────────────────────────────────────────────
def get_hoofdactie(triggers: list, holdings: list, market: dict,
                   sat_val_eur: float) -> str:
    """
    Rangschikt triggers en geeft één concrete actie terug.
    Prioriteit: earnings thesis-check > koopzone + DCA > concentratie > DCA > niets.
    """
    today = date.today()
    earnings_cfg = _load_earnings_config()

    # Prioriteit 1: earnings binnen 14 dagen → thesis-check
    for h in holdings:
        cfg_e = earnings_cfg.get(h["ticker"], {})
        if not cfg_e.get("date"):
            continue
        ed    = datetime.strptime(cfg_e["date"], "%Y-%m-%d").date()
        dagen = (ed - today).days
        if 0 <= dagen <= 14:
            return (f"Bereid thesis-check <strong>{h['name']}</strong> voor "
                    f"— rapport over <strong>{dagen} dagen</strong> ({ed.strftime('%d/%m/%Y')})")

    # Prioriteit 2: koopzone + DCA nadert → actie combineren
    next_dca  = next_dca_date()
    dagen_dca = (next_dca - today).days
    koopzone_items = []
    for ticker, info in WATCHLIST.items():
        md   = market.get(ticker, {})
        fpe  = md.get("forward_pe")
        hist = info.get("hist_pe") or HIST_PE.get(ticker)
        if not (fpe and hist and fpe < hist):
            continue
        egm = bereken_egm(fpe, hist, info.get("eps_growth_5y", 7),
                          info.get("div_yield", 2))
        if egm >= EGM_DREMPEL:
            koopzone_items.append((info["name"], fpe, egm))

    if koopzone_items and dagen_dca <= DCA_ALARM_DAGEN:
        naam, fpe, egm = koopzone_items[0]
        return (f"DCA over <strong>{dagen_dca} dagen</strong> — "
                f"<strong>{naam}</strong> in koopzone "
                f"(Fwd P/E {fpe:.1f}x, EGM {egm:.1f}%)")

    # Prioriteit 3: concentratie → expliciete inleg-regel
    for h in holdings:
        pct = round(h["eur_val"] / sat_val_eur * 100, 1) if sat_val_eur else 0
        if pct > CONCENTRATION_MAX:
            return (f"Volgende satelliet-inleg <strong>NIET naar {h['name']}</strong> "
                    f"({pct:.1f}% van satelliet > {CONCENTRATION_MAX}% drempel) "
                    f"— voeg eerst een nieuwe positie toe")

    # Prioriteit 4: DCA informationeel
    if dagen_dca <= DCA_ALARM_DAGEN:
        return (f"DCA over <strong>{dagen_dca} dagen</strong> — "
                f"screeen watchlist voor nieuwe positie (GEM 1 + GEM 2)")

    return "Geen actie vereist — systeem loopt"


# ── CAGR model ────────────────────────────────────────────────────────────────
def build_cagr_rows(etf_kern: float, sat_val: float) -> str:
    """Genereert een projectietabel op basis van CAGR_AANNAMES."""
    import calendar as cal
    c       = CAGR_AANNAMES
    today   = date.today()
    totaal  = etf_kern + sat_val
    blended = (0.8 * c["etf_cagr"] + 0.2 * c["satellite_cagr"]) / 100

    def project(jaren: int) -> float:
        waarde  = totaal
        maand   = 0
        for _ in range(jaren * 12):
            huidig_jaar = today.year + (today.month + maand - 1) // 12
            huidig_mnd  = (today.month + maand - 1) % 12 + 1
            proj_datum  = date(huidig_jaar, huidig_mnd, 1)
            inleg = c["dca_na_graduatie"] if proj_datum >= c["graduatie_datum"] else c["dca_nu"]
            waarde = waarde * (1 + blended / 12) + inleg
            maand += 1
        return round(waarde)

    mijlpalen = [(1, "2027"), (2, "2028"), (5, "2031"),
                 (10, "2036"), (20, "2046"), (30, "2056")]
    rijen = ""
    for jaren, jaar_label in mijlpalen:
        proj = project(jaren)
        graduatie_marker = " *" if jaren == 1 else ""
        rijen += (f"<tr><td>{jaar_label}{graduatie_marker}</td>"
                  f"<td>+{jaren}j</td>"
                  f"<td><strong>&euro;{proj:,.0f}</strong></td></tr>")
    return rijen


# ── Email bouwen ──────────────────────────────────────────────────────────────
def build_html(holdings: list, market: dict, fiscal: dict, triggers: list,
               history: dict, etf_kern: float) -> tuple:
    today_str    = date.today().strftime("%d/%m/%Y")
    today        = date.today()
    next_dca     = next_dca_date()
    dagen_dca    = (next_dca - today).days
    earnings_cfg = _load_earnings_config()

    sat_val_eur   = sum(h["eur_val"] for h in holdings)
    totaal        = sat_val_eur + etf_kern
    etf_pct       = round(etf_kern / totaal * 100, 1)
    sat_pct       = round(sat_val_eur / totaal * 100, 1)
    totaal_belegd = ETF_INVESTED + SAT_INVESTED
    meerwaarde    = fiscal.get("gerealiseerde_meerwaarde", 0)
    tob_ytd       = fiscal_tob_ytd(fiscal)
    tob_rate      = round(tob_ytd / totaal_belegd * 100, 2) if totaal_belegd else 0
    dividenden    = fiscal.get("dividenden_ontvangen", 0)

    # Subject
    urgente = [t for t in triggers if t.get("urgent")]
    if urgente:
        subject = f"[ALERT] Portfolio {today_str} — {urgente[0]['label'][:55]}"
    elif triggers:
        subject = f"[SIGNAAL] Portfolio {today_str} — {len(triggers)} punt(en)"
    else:
        subject = f"Portfolio Monitor {today_str} — Maandoverzicht"

    hoofdactie = get_hoofdactie(triggers, holdings, market, sat_val_eur)

    # ── Earnings binnen 30 dagen ──────────────────────────────────────────────
    earn_30_rijen = ""
    earn_later    = []
    naam_map = {h["ticker"]: h["name"] for h in holdings}
    naam_map.update({t: i["name"] for t, i in WATCHLIST.items()})
    alle_earn = [h["ticker"] for h in holdings] + list(WATCHLIST.keys())
    for ticker in alle_earn:
        cfg_e = earnings_cfg.get(ticker, {})
        if not cfg_e.get("date"):
            continue
        ed    = datetime.strptime(cfg_e["date"], "%Y-%m-%d").date()
        dagen = (ed - today).days
        naam  = naam_map.get(ticker, ticker)
        label = cfg_e.get("label", "")
        if 0 <= dagen <= 30:
            bg  = "#ffebee" if dagen <= 7 else "#fff8e1"
            urg = f"<strong style='color:#c62828'>{dagen}d</strong>" if dagen <= 7 else f"{dagen}d"
            earn_30_rijen += (
                f"<tr style='background:{bg}'><td><strong>{naam}</strong> "
                f"<small style='color:#666'>{label}</small></td>"
                f"<td>{ed.strftime('%d/%m/%Y')}</td><td>{urg}</td></tr>"
            )
        elif dagen > 30:
            earn_later.append(f"{naam} ({dagen}d)")

    earn_later_html = ""
    if earn_later:
        earn_later_html = (f"<p style='font-size:11px;color:#888;margin:4px 0'>"
                           f"Buiten 30 dagen: {', '.join(earn_later)}</p>")

    # Thesis-check blok voor earnings <= 14 dagen
    thesis_blok = ""
    for h in holdings:
        cfg_e = earnings_cfg.get(h["ticker"], {})
        if not cfg_e.get("date"):
            continue
        ed    = datetime.strptime(cfg_e["date"], "%Y-%m-%d").date()
        dagen = (ed - today).days
        if 0 <= dagen <= 14:
            checks = THESIS_CHECKS.get(h["symbol"], [])
            if checks:
                items = "".join(f"<li>{c}</li>" for c in checks)
                thesis_blok += (
                    f"<div style='background:#e8f5e9;border-left:4px solid #2e7d32;"
                    f"padding:10px 14px;margin:8px 0;border-radius:0 6px 6px 0'>"
                    f"<strong>Thesis-check {h['name']} — {ed.strftime('%d/%m/%Y')}</strong>"
                    f"<ul style='margin:6px 0;padding-left:18px;font-size:13px'>{items}</ul>"
                    f"</div>"
                )

    # ── Posities (thesis-review ipv stop-loss) ────────────────────────────────
    positie_rijen = ""
    for h in sorted(holdings, key=lambda x: x["eur_val"], reverse=True):
        pct       = round(h["eur_val"] / sat_val_eur * 100, 1) if sat_val_eur else 0
        bg        = "#ffebee" if pct > CONCENTRATION_MAX else ("#fff8e1" if pct > 28 else "")
        stale_tag = " <small style='color:#e65100'>[stale]</small>" if h.get("stale") else ""
        earn_cfg  = earnings_cfg.get(h["ticker"], {})
        review    = earn_cfg.get("date", "")
        review_str = (datetime.strptime(review, "%Y-%m-%d").strftime("%d/%m/%Y")
                      if review else "onbekend")
        positie_rijen += (
            f"<tr style='background:{bg}'>"
            f"<td><strong>{h['name']}</strong>{stale_tag}<br>"
            f"<small style='color:#666'>{h['symbol']} &middot; {h['sector']}</small></td>"
            f"<td align='right'>{h['qty']:.0f}</td>"
            f"<td align='right'><strong>{h['price']:.2f} {h['currency']}</strong></td>"
            f"<td align='right'>{h['value']:,.2f} {h['currency']}</td>"
            f"<td align='right'>{pct}%</td>"
            f"<td align='right' style='font-size:12px'>{review_str}</td></tr>"
        )

    # ── Watchlist + EGM ───────────────────────────────────────────────────────
    wl_koopzone = ""
    wl_overig   = ""
    for ticker, info in WATCHLIST.items():
        md        = market.get(ticker, {})
        prijs     = md.get("price")
        fpe       = md.get("forward_pe")
        hist      = info.get("hist_pe") or HIST_PE.get(ticker)
        prijs_str = f"{prijs:.2f}" if prijs else "&mdash;"
        fpe_str   = f"{fpe:.1f}x" if fpe else "&mdash;"
        hist_str  = f"{hist:.1f}x" if hist else "&mdash;"
        egm_str   = "&mdash;"
        status    = "&mdash;"
        is_koopzone = False
        if fpe and hist:
            delta = round((fpe / hist - 1) * 100, 1)
            if fpe < hist:
                egm = bereken_egm(fpe, hist,
                                  info.get("eps_growth_5y", 7),
                                  info.get("div_yield", 2))
                egm_str     = f"{egm:.1f}%"
                is_koopzone = egm >= EGM_DREMPEL
                kleur_egm   = "#2e7d32" if is_koopzone else "#e65100"
                geldig_tag  = "GELDIG" if is_koopzone else f"EGM {egm:.1f}% &lt; {EGM_DREMPEL}%"
                status = (f"<strong style='color:{kleur_egm}'>"
                          f"KOOPZONE {delta:+.1f}% | {geldig_tag}</strong>")
            else:
                status = f"{delta:+.1f}% boven hist. gem."
        rij = (f"<tr><td><strong>#{info['prio']} {info['name']}</strong><br>"
               f"<small style='color:#666'>{ticker} &middot; {info['sector']}</small></td>"
               f"<td align='right'>{prijs_str}</td>"
               f"<td align='right'>{fpe_str}</td>"
               f"<td align='right'>{hist_str}</td>"
               f"<td align='right'>{egm_str}</td>"
               f"<td>{status}</td></tr>")
        if is_koopzone:
            wl_koopzone += rij
        else:
            wl_overig += rij

    # ── Concentratie + Sectorspreiding ────────────────────────────────────────
    sectoren   = {}
    conc_items = []
    sector_kleuren = {
        "Healthcare": "#e53935", "Financials": "#1e88e5",
        "Technology": "#43a047", "Consumer Staples": "#fb8c00",
        "Industrials": "#8e24aa",
    }
    for h in holdings:
        pct = round(h["eur_val"] / sat_val_eur * 100, 1) if sat_val_eur else 0
        sectoren[h["sector"]] = sectoren.get(h["sector"], 0) + h["eur_val"]
        if pct > CONCENTRATION_MAX:
            conc_items.append(
                f"<div style='background:#ffebee;border-left:4px solid #c62828;"
                f"padding:8px 12px;margin:4px 0;border-radius:0 6px 6px 0'>"
                f"<strong style='color:#c62828'>{h['name']}: {pct}% van satelliet</strong>"
                f" (drempel {CONCENTRATION_MAX}%) &mdash; "
                f"<strong>volgende inleg NIET naar {h['name']}</strong></div>"
            )
    sector_bars = ""
    for sector, val in sorted(sectoren.items(), key=lambda x: x[1], reverse=True):
        pct   = round(val / sat_val_eur * 100, 1) if sat_val_eur else 0
        kleur = sector_kleuren.get(sector, "#90a4ae")
        sector_bars += (
            f"<tr><td style='width:140px;font-size:13px'>{sector}</td>"
            f"<td><div style='background:{kleur};width:{int(pct*2)}px;height:16px;"
            f"border-radius:3px;display:inline-block'></div> {pct}%</td></tr>"
        )
    for s in ["Consumer Staples", "Industrials"]:
        if s not in sectoren:
            sector_bars += (
                f"<tr><td style='color:#999;font-size:13px'>{s}</td>"
                f"<td style='color:#999;font-size:12px'>0% &rarr; volgende aankoop</td></tr>"
            )

    tob_context  = f"effectieve rate {tob_rate:.2f}% (optimaal door IE-gevestigde ETF)"
    cagr_blended = round(0.8 * CAGR_AANNAMES["etf_cagr"] + 0.2 * CAGR_AANNAMES["satellite_cagr"], 1)
    cagr_rijen   = build_cagr_rows(etf_kern, sat_val_eur)
    grad_datum   = CAGR_AANNAMES["graduatie_datum"].strftime("%B %Y")

    stale_html = ""
    stale_syms = [h["name"] for h in holdings if h.get("stale")]
    if stale_syms:
        stale_html = (
            f"<div style='background:#fff8e1;border-left:4px solid #ffa000;"
            f"padding:8px 12px;margin:8px 0'>Waarschuwing: geen actuele koers voor "
            f"{', '.join(stale_syms)}.</div>"
        )

    dca_kleur = "color:#c62828;font-weight:bold" if dagen_dca <= DCA_ALARM_DAGEN else ""

    wl_koopzone_table = (
        "<table width=\"100%\" cellpadding=\"6\" cellspacing=\"0\" "
        "style=\"border-collapse:collapse;font-size:13px\">"
        "<tr style=\"background:#e8f5e9\"><th align=\"left\">Bedrijf</th>"
        "<th align=\"right\">Koers</th><th align=\"right\">Fwd P/E</th>"
        "<th align=\"right\">Hist.</th><th align=\"right\">EGM</th>"
        "<th align=\"left\">Oordeel</th></tr>"
        + wl_koopzone + "</table>"
    ) if wl_koopzone else ""

    wl_overig_table = (
        "<table width=\"100%\" cellpadding=\"6\" cellspacing=\"0\" "
        "style=\"border-collapse:collapse;font-size:13px;margin-top:4px\">"
        "<tr style=\"background:#f5f5f5\"><th align=\"left\">Bedrijf</th>"
        "<th align=\"right\">Koers</th><th align=\"right\">Fwd P/E</th>"
        "<th align=\"right\">Hist.</th><th align=\"right\">EGM</th>"
        "<th align=\"left\">Status</th></tr>"
        + wl_overig + "</table>"
    ) if wl_overig else ""

    earn_table = (
        "<table width=\"100%\" cellpadding=\"6\" cellspacing=\"0\" "
        "style=\"border-collapse:collapse;font-size:13px\">"
        "<tr style=\"background:#f5f5f5\"><th align=\"left\">Bedrijf</th>"
        "<th align=\"left\">Datum</th><th align=\"left\">Afstand</th></tr>"
        + earn_30_rijen + "</table>"
    ) if earn_30_rijen else "<p style=\"color:#888;font-size:13px\">Geen earnings binnen 30 dagen.</p>"

    html = f"""<!DOCTYPE html>
<html><body style="font-family:Arial,sans-serif;max-width:700px;margin:auto;color:#333;font-size:14px">

<h2 style="color:#1565c0;margin-bottom:4px">Portfolio Monitor &mdash; {today_str}</h2>
<p style="color:#888;font-size:12px;margin-top:0">Yahoo Finance &middot; PP1&ndash;PP3 + KB1&ndash;KB10</p>

<table width="100%" cellpadding="10" style="background:#e3f2fd;border-radius:8px;margin-bottom:12px;border-collapse:collapse">
<tr>
  <td><div style="font-size:11px;color:#555">TOTAAL BELEGD</div>
      <div style="font-size:22px;font-weight:bold;color:#1565c0">&euro;{totaal:,.2f}</div></td>
  <td><div style="font-size:11px;color:#555">ETF KERN</div>
      <div style="font-size:15px">&euro;{etf_kern:,.2f} <span style="color:#2e7d32">({etf_pct}%)</span></div></td>
  <td><div style="font-size:11px;color:#555">SATELLIET</div>
      <div style="font-size:15px">&euro;{sat_val_eur:,.2f} <span style="color:#1565c0">({sat_pct}%)</span></div></td>
  <td><div style="font-size:11px;color:#555">DCA</div>
      <div style="font-size:15px;{dca_kleur}">{dagen_dca}d &mdash; {next_dca.strftime('%d/%m/%Y')}</div></td>
</tr>
</table>

{stale_html}

<div style="background:#1565c0;color:white;padding:12px 16px;border-radius:6px;margin-bottom:16px;font-size:15px">
<span style="font-size:11px;opacity:0.8;display:block;margin-bottom:2px">ACTIE VAN VANDAAG</span>
{hoofdactie}
</div>

<h3 style="border-bottom:2px solid #e3f2fd;padding-bottom:4px;margin-top:0">Earnings binnen 30 dagen</h3>
{earn_table}
{earn_later_html}
{thesis_blok}

<h3 style="border-bottom:2px solid #e3f2fd;padding-bottom:4px;margin-top:20px">Posities</h3>
<p style="font-size:11px;color:#999;margin:0 0 6px">Aankoopprijs verborgen. Thesis-review = eerstvolgende earnings.</p>
<table width="100%" cellpadding="6" cellspacing="0" style="border-collapse:collapse;font-size:13px">
<tr style="background:#f5f5f5">
<th align="left">Positie</th><th align="right">Qty</th><th align="right">Koers</th>
<th align="right">Waarde</th><th align="right">% Sat.</th><th align="right">Thesis-review</th>
</tr>
{positie_rijen}
</table>

<h3 style="border-bottom:2px solid #e3f2fd;padding-bottom:4px;margin-top:20px">Watchlist &mdash; EGM koopzone check (&ge; {EGM_DREMPEL}%)</h3>
{wl_koopzone_table}
{wl_overig_table}
<p style="font-size:11px;color:#888">EGM = EPS groei + dividend yield + P/E mean reversion (5j horizon). KOOPZONE = valide enkel als EGM &ge; {EGM_DREMPEL}%.</p>

<h3 style="border-bottom:2px solid #e3f2fd;padding-bottom:4px;margin-top:20px">Concentratie &amp; Sectorspreiding</h3>
{"".join(conc_items) if conc_items else '<p style="color:#2e7d32;font-size:13px">Geen concentratiewaarschuwingen.</p>'}
<table cellpadding="4" cellspacing="0" style="margin-top:8px">{sector_bars}</table>

<h3 style="border-bottom:2px solid #e3f2fd;padding-bottom:4px;margin-top:20px">Fiscaal &amp; Planning 2026</h3>
<table width="100%" cellpadding="8" cellspacing="0" style="border-collapse:collapse;font-size:13px">
<tr>
  <td style="background:#f5f5f5;width:34%">
    <div style="font-size:11px;color:#666">MEERWAARDE YTD</div>
    <div style="font-size:17px;font-weight:bold">&euro;{meerwaarde:,.2f}</div>
    <div style="font-size:11px;color:#888">/ &euro;10.000 vrijstelling</div>
  </td>
  <td style="background:#f5f5f5;width:33%;padding-left:12px">
    <div style="font-size:11px;color:#666">TOB YTD</div>
    <div style="font-size:17px;font-weight:bold">&euro;{tob_ytd:,.2f}</div>
    <div style="font-size:11px;color:#888">{tob_context}</div>
  </td>
  <td style="background:#f5f5f5;width:33%;padding-left:12px">
    <div style="font-size:11px;color:#666">DIVIDENDEN YTD</div>
    <div style="font-size:17px;font-weight:bold">&euro;{dividenden:,.2f}</div>
    <div style="font-size:11px;color:#888">eerste &euro;800/persoon terugvorderbaar</div>
  </td>
</tr>
</table>
<div style="background:#f5f5f5;padding:8px 12px;margin-top:6px;font-size:13px;border-radius:4px">
<strong>Volgende DCA: {next_dca.strftime('%d/%m/%Y')} ({dagen_dca} dagen)</strong>
&mdash; 80% &rarr; IMIE.MI (Bolero) &middot; 20% &rarr; nieuwe positie (GEM 1 + GEM 2 vereist)
</div>

<h3 style="border-bottom:2px solid #e3f2fd;padding-bottom:4px;margin-top:20px">Vermogensgroei-projectie</h3>
<p style="font-size:12px;color:#888;margin:0 0 6px">
ETF {CAGR_AANNAMES["etf_cagr"]}% + satelliet {CAGR_AANNAMES["satellite_cagr"]}% = gemengd <strong>{cagr_blended}%</strong> CAGR &middot;
DCA &euro;{CAGR_AANNAMES["dca_nu"]}/kwartaal nu, &euro;{CAGR_AANNAMES["dca_na_graduatie"]}/mnd na graduatie ({grad_datum})
</p>
<table cellpadding="6" cellspacing="0" style="border-collapse:collapse;font-size:13px">
<tr style="background:#f5f5f5"><th align="left">Jaar</th><th align="left">Horizon</th><th align="left">Geschatte waarde</th></tr>
{cagr_rijen}
</table>
<p style="font-size:11px;color:#bbb">
* Graduatie {grad_datum}: inleg &euro;{CAGR_AANNAMES["dca_nu"]}/mnd &rarr; &euro;{CAGR_AANNAMES["dca_na_graduatie"]}/mnd &middot;
Niet gecorrigeerd voor inflatie &middot; Noodbuffer &euro;{CAGR_AANNAMES["noodbuffer"]:,} niet meegeteld
</p>
<p style="font-size:11px;color:#bbb;font-style:italic">Performance ETF vs. satelliet: eerste meting op 27/02/2027 (12 maanden vereist).</p>

<hr style="margin:16px 0;border:none;border-top:1px solid #e0e0e0">
<p style="color:#bbb;font-size:11px">Portfolio Monitor v3 &middot; {today} &middot; Yahoo Finance</p>
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

    print("2. Portfolio ophalen (Yahoo Finance)...")
    raw      = _get_portfolio_offline()
    holdings = raw["holdings"]
    sat_val_eur = sum(h["eur_val"] for h in holdings)
    print(f"   {len(holdings)} posities | Satelliet EUR {raw['total_value']:,.2f}")

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
