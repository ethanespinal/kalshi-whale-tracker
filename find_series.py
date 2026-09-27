"""Manual series inspection only; never called by the alert pipeline."""
from kalshi import KalshiClient
from series_map import SERIES_FAMILIES


def main():
    client = KalshiClient()
    try:
        configured = {ticker for family in SERIES_FAMILIES.values()
                      for tickers in family.values() for ticker in tickers}
        data = client.get("/series", {"category": "Sports"})
        series = data.get("series", [])
        for item in sorted(series, key=lambda item: item["ticker"]):
            ticker = item["ticker"]
            if ticker in configured:
                print(ticker, "|", item.get("title", ""))
        missing = configured - {s["ticker"] for s in series}
        if missing:
            print("Configured series absent:", ", ".join(sorted(missing)))
    finally:
        client.close()


if __name__ == "__main__":
    main()
