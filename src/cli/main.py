from src.core.config import get_settings
from src.data.db_client import AzureSqlClient
from src.services.stock_search import find_stock_symbol
from src.services.options_service import process_underlying_once

def run() -> None:
    settings = get_settings()

    query_name = input("Enter stock or index name (e.g., Reliance, NIFTY): ").strip()
    if not query_name:
        print("No input given, exiting.")
        return

    # 1) Resolve the user-friendly name to an underlying / tradingsymbol

    db = AzureSqlClient(settings)
    db.connect()
    stock = find_stock_symbol(db, query_name)
    db.close()

    if stock is None:
        return

    underlying_symbol = stock.tradingsymbol.upper()
    print(f"\nUsing underlying symbol: {underlying_symbol}")

    # 2) Refresh data: fetch from Kite, upsert instruments, insert snapshots
    contracts, snapshots = process_underlying_once(underlying_symbol, settings)
    print(f"Processed {contracts} contracts, inserted {snapshots} snapshots.")

     # 3) Optional: show how many latest rows are now available in the DB view
    db = AzureSqlClient(settings)
    db.connect()
    latest_chain = db.fetch_latest_option_chain_for_underlying(underlying_symbol)
    db.close()

    print(f"Latest chain in DB for {underlying_symbol}: {len(latest_chain)} rows.")
    if latest_chain:
        # Print a small sample so you can sanity check from CLI
        print("Sample row:")
        sample = latest_chain[0]
        print(
            f"{sample['tradingsymbol']} | "
            f"Strike={sample['strike']} | "
            f"Type={sample['instrument_type']} | "
            f"LTP={sample['last_price']} | "
            f"IV={sample['implied_volatility']}"
        )


if __name__ == "__main__":
    run()


