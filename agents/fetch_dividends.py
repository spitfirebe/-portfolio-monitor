"""
Haalt dividenden op via Yahoo Finance voor alle posities.
Berekent ontvangen dividenden na aankoopdatum.
"""
import urllib.request, urllib.parse, json, sys, os
from datetime import datetime

sys.path.insert(0, os.path.dirname(__file__))
from config import POSITIONS, FISCAL_FILE

# Aankoopdatums per Saxo-symbool (uit PP2)
AANKOOP_DATUMS = {
    "UCB:xbru":  "2026-04-20",
    "HLNE:xnas": "2026-04-17",
    "AOF:xetr":  "2026-04-14",
    "V:xnys":    "2026-04-02",
}

def fetch_dividends_since(ticker: str, since_str: str, qty: int) -> list:
    since_ts = int(datetime.strptime(since_str, "%Y-%m-%d").timestamp())
    enc = urllib.parse.quote(ticker)
    url = (f"https://query1.finance.yahoo.com/v8/finance/chart/{enc}"
           f"?period1={since_ts}&period2=1800000000&interval=1d&events=dividends")
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    results = []
    try:
        with urllib.request.urlopen(req, timeout=8) as r:
            d = json.loads(r.read())
        divs = (d.get("chart", {}).get("result", [{}])[0]
                 .get("events", {}).get("dividends", {}))
        for ts, v in divs.items():
            ex_date = datetime.fromtimestamp(int(ts)).date()
            amount  = v.get("amount", 0)
            results.append({
                "ex_date":    str(ex_date),
                "amount_per_share": round(amount, 4),
                "qty":        qty,
                "total":      round(amount * qty, 2),
            })
    except Exception as e:
        print(f"  Fout voor {ticker}: {e}")
    return results

def update_fiscal(dividenden: list):
    """Schrijft ontvangen dividenden naar fiscal_2026.json."""
    with open(FISCAL_FILE, encoding="utf-8") as f:
        fiscal = json.load(f)

    bestaande = {d["ex_date"] + d.get("ticker","") for d in fiscal.get("dividenden", [])}
    nieuw = 0
    for d in dividenden:
        key = d["ex_date"] + d["ticker"]
        if key not in bestaande:
            fiscal["dividenden"].append(d)
            nieuw += 1

    total_div = sum(d["total"] for d in fiscal.get("dividenden", []))
    fiscal["dividenden_ontvangen"] = round(total_div, 2)

    with open(FISCAL_FILE, "w", encoding="utf-8") as f:
        json.dump(fiscal, f, indent=2, ensure_ascii=False)

    return nieuw, total_div

def main():
    print("Dividenden ophalen via Yahoo Finance...")
    alle_dividenden = []

    for sym, cfg in POSITIONS.items():
        ticker   = cfg["ticker"]
        qty      = cfg["qty"]
        since    = AANKOOP_DATUMS.get(sym, "2026-01-01")
        currency = cfg["currency"]

        divs = fetch_dividends_since(ticker, since, qty)
        if divs:
            for d in divs:
                d["ticker"]   = ticker
                d["symbol"]   = sym
                d["currency"] = currency
                alle_dividenden.append(d)
                print(f"  {ticker}: {d['ex_date']} - "
                      f"{d['amount_per_share']}/aandeel x {qty} = "
                      f"{d['total']} {currency}")
        else:
            print(f"  {ticker}: geen dividenden na {since}")

    if alle_dividenden:
        nieuw, totaal = update_fiscal(alle_dividenden)
        print(f"\nFiscale tracker bijgewerkt: {nieuw} nieuwe dividenden, totaal EUR/USD {totaal:.2f}")
    else:
        print("\nGeen dividenden ontvangen — fiscal tracker ongewijzigd.")

    # Toon verwachte komende dividenden
    print("\nVerwachte komende dividenden (schatting):")
    print("  HLNE:   ~$0.47/kwartaal x 6 = $2.82  (volgende: ~jun 2026)")
    print("  V:      ~$0.59/kwartaal x 1 = $0.59  (volgende: ~jun 2026)")
    print("  UCB:    jaarlijks dividend   (volgende: ~mei/jun 2026)")
    print("  ATOSS:  EUR 2.28 distributie (controleer ex-dividend datum)")

if __name__ == "__main__":
    main()
