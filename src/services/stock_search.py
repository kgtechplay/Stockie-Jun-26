# src/stock_search.py
from typing import Optional

from src.data.db_client import AzureSqlClient
from src.domain.models import StockInstrument


def find_stock_symbol(
    db: AzureSqlClient,
    query_name: str,
    limit: int = 10,
) -> Optional[StockInstrument]:
    """
    Search StockDB by partial name / symbol and let the user pick one.
    Returns the chosen StockInstrument (or None if no selection).
    """
    matches = db.search_stocks_by_name(query_name, limit=limit)

    if not matches:
        print(f"No stocks found matching '{query_name}'.")
        return None

    print(f"Found {len(matches)} matches for '{query_name}':\n")
    for idx, s in enumerate(matches, start=1):
        print(f"{idx}. {s.tradingsymbol} - {s.name} ({s.exchange})")

    while True:
        choice = input(
            "\nEnter the number of the correct stock "
            "(or press Enter to cancel): "
        ).strip()

        if choice == "":
            print("Cancelled selection.")
            return None

        if not choice.isdigit():
            print("Please enter a valid number.")
            continue

        i = int(choice)
        if not (1 <= i <= len(matches)):
            print("Choice out of range. Try again.")
            continue

        selected = matches[i - 1]
        print(
            f"\nYou selected: {selected.tradingsymbol} - "
            f"{selected.name} ({selected.exchange})"
        )
        confirm = input("Confirm? [y/N]: ").strip().lower()
        if confirm == "y":
            return selected
        else:
            print("Selection not confirmed, please choose again.")


