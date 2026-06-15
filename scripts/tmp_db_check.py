import sys
sys.path.insert(0, '.')
from dotenv import load_dotenv
load_dotenv()
from src.common.config import get_settings
from src.data_manager.db.client_factory import get_database_client

settings = get_settings()
db = get_database_client(settings)
db.connect()

with db.conn.cursor() as cur:
    # Underlying OHLC - last 5 days
    cur.execute("""
        SELECT underlying, trade_date, open_price, high_price, low_price, close_price, volume
        FROM "UnderlyingSnapshot"
        WHERE underlying = 'NIFTY'
        ORDER BY trade_date DESC
        LIMIT 5
    """)
    rows = cur.fetchall()
    print("=== UnderlyingSnapshot (last 5 rows) ===")
    print(f"{'underlying':<12} {'trade_date':<12} {'open':>8} {'high':>8} {'low':>8} {'close':>8} {'volume':>10}")
    print("-" * 70)
    for r in rows:
        print(f"{str(r[0]):<12} {str(r[1]):<12} {str(r[2] or ''):>8} {str(r[3] or ''):>8} {str(r[4] or ''):>8} {str(r[5] or ''):>8} {str(r[6] or ''):>10}")

    print()

    # Option snapshots - last 2 days summary
    cur.execute("""
        SELECT
            os.trade_date,
            os.snapshot_label,
            os.data_source,
            COUNT(*) AS rows,
            COUNT(os.bid_price) AS with_bid
        FROM "OptionSnapshot" os
        WHERE os.trade_date >= CURRENT_DATE - INTERVAL '2 days'
        GROUP BY os.trade_date, os.snapshot_label, os.data_source
        ORDER BY os.trade_date DESC, os.snapshot_label
    """)
    rows = cur.fetchall()
    print("=== OptionSnapshot (last 2 days) ===")
    if not rows:
        print("No option snapshot data found for last 2 days.")
    else:
        print(f"{'trade_date':<12} {'label':<20} {'rows':<8} {'w/bid':<8} {'source'}")
        print("-" * 75)
        for r in rows:
            print(f"{str(r[0]):<12} {str(r[1]):<20} {r[3]:<8} {r[4]:<8} {str(r[2] or '')}")

db.close()
