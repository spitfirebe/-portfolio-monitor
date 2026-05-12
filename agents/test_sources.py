import urllib.request, urllib.parse, json
from datetime import datetime

def yf_timeseries_earnings(symbol):
    """Probeer toekomstige eps-schattingen te halen — bevatten soms earnings datum."""
    enc = urllib.parse.quote(symbol)
    url = (f"https://query1.finance.yahoo.com/ws/fundamentals-timeseries/v1/finance/"
           f"timeseries/{enc}?type=quarterlyEpsEstimate,quarterlyDilutedEps"
           f"&period1=1735000000&period2=1800000000")
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(req, timeout=8) as r:
            d = json.loads(r.read())
        results = d.get("timeseries", {}).get("result", [])
        timestamps = d.get("timeseries", {}).get("timestamp", [])
        output = {}
        for r in results:
            t = r.get("meta", {}).get("type", ["?"])[0]
            vals = r.get("quarterlyEpsEstimate") or r.get("quarterlyDilutedEps") or []
            if vals:
                dates = [datetime.fromtimestamp(v["asOfDate"]//1000 if v.get("asOfDate", 0) > 1e10
                         else v["asOfDate"]).strftime("%Y-%m-%d")
                         for v in vals if isinstance(v, dict) and v.get("asOfDate")]
                output[t] = dates
        if timestamps:
            output["raw_timestamps"] = [datetime.fromtimestamp(ts).strftime("%Y-%m-%d") for ts in timestamps]
        return output
    except Exception as e:
        return {"error": str(e)}

def alpha_vantage_demo(symbol):
    """Alpha Vantage earnings kalender met demo key."""
    clean = symbol.split(".")[0]
    url = f"https://www.alphavantage.co/query?function=EARNINGS_CALENDAR&symbol={clean}&horizon=3month&apikey=demo"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(req, timeout=8) as r:
            content = r.read().decode("utf-8")
        return content[:200]
    except Exception as e:
        return f"FOUT: {e}"

print("=== YF TIMESERIES EPS ESTIMATES (toekomstige datums) ===")
for sym in ["V", "HLNE", "UCB.BR", "AOF.DE"]:
    r = yf_timeseries_earnings(sym)
    print(f"  {sym}: {r}")

print("\n=== ALPHA VANTAGE DEMO ===")
for sym in ["V", "HLNE"]:
    print(f"  {sym}: {alpha_vantage_demo(sym)}")
