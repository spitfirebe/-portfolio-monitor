"""Vervangt build_html in daily_routine.py met de nieuwe versie."""
import sys, os
sys.stdout.reconfigure(encoding="utf-8")

with open("daily_routine.py", encoding="utf-8") as f:
    content = f.read()

start_marker = "def build_html(holdings: list, market: dict, fiscal: dict, triggers: list,"
end_marker   = "\n\n\n# ── Email versturen"

start_idx = content.find(start_marker)
end_idx   = content.find(end_marker, start_idx)

if start_idx == -1 or end_idx == -1:
    print(f"Markers niet gevonden! start={start_idx}, end={end_idx}")
    sys.exit(1)

print(f"build_html gevonden: chars {start_idx}-{end_idx}")
before = content[:start_idx]
after  = content[end_idx:]

NEW_BUILD_HTML = '''def build_html(holdings: list, market: dict, fiscal: dict, triggers: list,
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
        subject = f"[ALERT] Portfolio {today_str} — {urgente[0][\'label\'][:55]}"
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
            urg = f"<strong style=\'color:#c62828\'>{dagen}d</strong>" if dagen <= 7 else f"{dagen}d"
            earn_30_rijen += (
                f"<tr style=\'background:{bg}\'><td><strong>{naam}</strong> "
                f"<small style=\'color:#666\'>{label}</small></td>"
                f"<td>{ed.strftime(\'%d/%m/%Y\')}</td><td>{urg}</td></tr>"
            )
        elif dagen > 30:
            earn_later.append(f"{naam} ({dagen}d)")

    earn_later_html = ""
    if earn_later:
        earn_later_html = (f"<p style=\'font-size:11px;color:#888;margin:4px 0\'>"
                           f"Buiten 30 dagen: {\', \'.join(earn_later)}</p>")

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
                    f"<div style=\'background:#e8f5e9;border-left:4px solid #2e7d32;"
                    f"padding:10px 14px;margin:8px 0;border-radius:0 6px 6px 0\'>"
                    f"<strong>Thesis-check {h[\'name\']} — {ed.strftime(\'%d/%m/%Y\')}</strong>"
                    f"<ul style=\'margin:6px 0;padding-left:18px;font-size:13px\'>{items}</ul>"
                    f"</div>"
                )

    # ── Posities (thesis-review ipv stop-loss) ────────────────────────────────
    positie_rijen = ""
    for h in sorted(holdings, key=lambda x: x["eur_val"], reverse=True):
        pct       = round(h["eur_val"] / sat_val_eur * 100, 1) if sat_val_eur else 0
        bg        = "#ffebee" if pct > CONCENTRATION_MAX else ("#fff8e1" if pct > 28 else "")
        stale_tag = " <small style=\'color:#e65100\'>[stale]</small>" if h.get("stale") else ""
        earn_cfg  = earnings_cfg.get(h["ticker"], {})
        review    = earn_cfg.get("date", "")
        review_str = (datetime.strptime(review, "%Y-%m-%d").strftime("%d/%m/%Y")
                      if review else "onbekend")
        positie_rijen += (
            f"<tr style=\'background:{bg}\'>"
            f"<td><strong>{h[\'name\']}</strong>{stale_tag}<br>"
            f"<small style=\'color:#666\'>{h[\'symbol\']} &middot; {h[\'sector\']}</small></td>"
            f"<td align=\'right\'>{h[\'qty\']:.0f}</td>"
            f"<td align=\'right\'><strong>{h[\'price\']:.2f} {h[\'currency\']}</strong></td>"
            f"<td align=\'right\'>{h[\'value\']:,.2f} {h[\'currency\']}</td>"
            f"<td align=\'right\'>{pct}%</td>"
            f"<td align=\'right\' style=\'font-size:12px\'>{review_str}</td></tr>"
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
                status = (f"<strong style=\'color:{kleur_egm}\'>"
                          f"KOOPZONE {delta:+.1f}% | {geldig_tag}</strong>")
            else:
                status = f"{delta:+.1f}% boven hist. gem."
        rij = (f"<tr><td><strong>#{info[\'prio\']} {info[\'name\']}</strong><br>"
               f"<small style=\'color:#666\'>{ticker} &middot; {info[\'sector\']}</small></td>"
               f"<td align=\'right\'>{prijs_str}</td>"
               f"<td align=\'right\'>{fpe_str}</td>"
               f"<td align=\'right\'>{hist_str}</td>"
               f"<td align=\'right\'>{egm_str}</td>"
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
                f"<div style=\'background:#ffebee;border-left:4px solid #c62828;"
                f"padding:8px 12px;margin:4px 0;border-radius:0 6px 6px 0\'>"
                f"<strong style=\'color:#c62828\'>{h[\'name\']}: {pct}% van satelliet</strong>"
                f" (drempel {CONCENTRATION_MAX}%) &mdash; "
                f"<strong>volgende inleg NIET naar {h[\'name\']}</strong></div>"
            )
    sector_bars = ""
    for sector, val in sorted(sectoren.items(), key=lambda x: x[1], reverse=True):
        pct   = round(val / sat_val_eur * 100, 1) if sat_val_eur else 0
        kleur = sector_kleuren.get(sector, "#90a4ae")
        sector_bars += (
            f"<tr><td style=\'width:140px;font-size:13px\'>{sector}</td>"
            f"<td><div style=\'background:{kleur};width:{int(pct*2)}px;height:16px;"
            f"border-radius:3px;display:inline-block\'></div> {pct}%</td></tr>"
        )
    for s in ["Consumer Staples", "Industrials"]:
        if s not in sectoren:
            sector_bars += (
                f"<tr><td style=\'color:#999;font-size:13px\'>{s}</td>"
                f"<td style=\'color:#999;font-size:12px\'>0% &rarr; volgende aankoop</td></tr>"
            )

    tob_context  = f"effectieve rate {tob_rate:.2f}% (optimaal door IE-gevestigde ETF)"
    cagr_blended = round(0.8 * CAGR_AANNAMES["etf_cagr"] + 0.2 * CAGR_AANNAMES["satellite_cagr"], 1)
    cagr_rijen   = build_cagr_rows(etf_kern, sat_val_eur)
    grad_datum   = CAGR_AANNAMES["graduatie_datum"].strftime("%B %Y")

    stale_html = ""
    stale_syms = [h["name"] for h in holdings if h.get("stale")]
    if stale_syms:
        stale_html = (
            f"<div style=\'background:#fff8e1;border-left:4px solid #ffa000;"
            f"padding:8px 12px;margin:8px 0\'>Waarschuwing: geen actuele koers voor "
            f"{\', \'.join(stale_syms)}.</div>"
        )

    dca_kleur = "color:#c62828;font-weight:bold" if dagen_dca <= DCA_ALARM_DAGEN else ""

    wl_koopzone_table = (
        "<table width=\\"100%\\" cellpadding=\\"6\\" cellspacing=\\"0\\" "
        "style=\\"border-collapse:collapse;font-size:13px\\">"
        "<tr style=\\"background:#e8f5e9\\"><th align=\\"left\\">Bedrijf</th>"
        "<th align=\\"right\\">Koers</th><th align=\\"right\\">Fwd P/E</th>"
        "<th align=\\"right\\">Hist.</th><th align=\\"right\\">EGM</th>"
        "<th align=\\"left\\">Oordeel</th></tr>"
        + wl_koopzone + "</table>"
    ) if wl_koopzone else ""

    wl_overig_table = (
        "<table width=\\"100%\\" cellpadding=\\"6\\" cellspacing=\\"0\\" "
        "style=\\"border-collapse:collapse;font-size:13px;margin-top:4px\\">"
        "<tr style=\\"background:#f5f5f5\\"><th align=\\"left\\">Bedrijf</th>"
        "<th align=\\"right\\">Koers</th><th align=\\"right\\">Fwd P/E</th>"
        "<th align=\\"right\\">Hist.</th><th align=\\"right\\">EGM</th>"
        "<th align=\\"left\\">Status</th></tr>"
        + wl_overig + "</table>"
    ) if wl_overig else ""

    earn_table = (
        "<table width=\\"100%\\" cellpadding=\\"6\\" cellspacing=\\"0\\" "
        "style=\\"border-collapse:collapse;font-size:13px\\">"
        "<tr style=\\"background:#f5f5f5\\"><th align=\\"left\\">Bedrijf</th>"
        "<th align=\\"left\\">Datum</th><th align=\\"left\\">Afstand</th></tr>"
        + earn_30_rijen + "</table>"
    ) if earn_30_rijen else "<p style=\\"color:#888;font-size:13px\\">Geen earnings binnen 30 dagen.</p>"

    html = f"""<!DOCTYPE html>
<html><body style="font-family:Arial,sans-serif;max-width:700px;margin:auto;color:#333;font-size:14px">

<h2 style="color:#1565c0;margin-bottom:4px">Portfolio Monitor &mdash; {today_str}</h2>
<p style="color:#888;font-size:12px;margin-top:0">Saxo Bank + Yahoo Finance &middot; PP1&ndash;PP3 + KB1&ndash;KB10</p>

<table width="100%" cellpadding="10" style="background:#e3f2fd;border-radius:8px;margin-bottom:12px;border-collapse:collapse">
<tr>
  <td><div style="font-size:11px;color:#555">TOTAAL BELEGD</div>
      <div style="font-size:22px;font-weight:bold;color:#1565c0">&euro;{totaal:,.2f}</div></td>
  <td><div style="font-size:11px;color:#555">ETF KERN</div>
      <div style="font-size:15px">&euro;{etf_kern:,.2f} <span style="color:#2e7d32">({etf_pct}%)</span></div></td>
  <td><div style="font-size:11px;color:#555">SATELLIET</div>
      <div style="font-size:15px">&euro;{sat_val_eur:,.2f} <span style="color:#1565c0">({sat_pct}%)</span></div></td>
  <td><div style="font-size:11px;color:#555">DCA</div>
      <div style="font-size:15px;{dca_kleur}">{dagen_dca}d &mdash; {next_dca.strftime(\'%d/%m/%Y\')}</div></td>
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
{"".join(conc_items) if conc_items else \'<p style="color:#2e7d32;font-size:13px">Geen concentratiewaarschuwingen.</p>\'}
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
<strong>Volgende DCA: {next_dca.strftime(\'%d/%m/%Y\')} ({dagen_dca} dagen)</strong>
&mdash; 80% &rarr; IMIE.MI (Bolero) &middot; 20% &rarr; nieuwe positie (GEM 1 + GEM 2 vereist)
</div>

<h3 style="border-bottom:2px solid #e3f2fd;padding-bottom:4px;margin-top:20px">Vermogensgroei-projectie</h3>
<p style="font-size:12px;color:#888;margin:0 0 6px">
ETF {CAGR_AANNAMES["etf_cagr"]}% + satelliet {CAGR_AANNAMES["satellite_cagr"]}% = gemengd <strong>{cagr_blended}%</strong> CAGR &middot;
DCA &euro;{CAGR_AANNAMES["dca_nu"]}/mnd nu, &euro;{CAGR_AANNAMES["dca_na_graduatie"]}/mnd na graduatie ({grad_datum})
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
<p style="color:#bbb;font-size:11px">Portfolio Monitor v3 &middot; {today} &middot; Saxo Bank + Yahoo Finance</p>
</body></html>"""

    return subject, html

'''

new_content = before + NEW_BUILD_HTML + after
with open("daily_routine.py", "w", encoding="utf-8") as f:
    f.write(new_content)
print(f"Succesvol geschreven: {len(new_content)} chars")
