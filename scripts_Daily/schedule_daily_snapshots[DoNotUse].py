# schedule_daily_snapshots.py
"""
Scheduler script to run daily_intraday_stock_option.py at:
- 9:20 AM IST
- 3:20 PM IST (15:20 IST)

This script runs continuously and executes the daily snapshot script at the scheduled times.
"""

import sys
import subprocess
from pathlib import Path
from datetime import datetime, time as dtime
import pytz
import schedule
import time

# Add project root to Python path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

# IST timezone
IST = pytz.timezone('Asia/Kolkata')

# Path to the script to run
SCRIPT_PATH = project_root / "scripts" / "daily_intraday_stock_option.py"


def run_daily_snapshot():
    """Execute the daily_intraday_stock_option.py script."""
    print(f"\n{'='*60}")
    print(f"[{datetime.now(IST).strftime('%Y-%m-%d %H:%M:%S IST')}] Starting daily snapshot collection...")
    print(f"{'='*60}\n")
    
    try:
        # Run the script using the same Python interpreter
        result = subprocess.run(
            [sys.executable, str(SCRIPT_PATH)],
            cwd=str(project_root),
            capture_output=False,  # Show output in real-time
            text=True
        )
        
        if result.returncode == 0:
            print(f"\n[{datetime.now(IST).strftime('%Y-%m-%d %H:%M:%S IST')}] Daily snapshot completed successfully.")
        else:
            print(f"\n[{datetime.now(IST).strftime('%Y-%m-%d %H:%M:%S IST')}] Daily snapshot failed with exit code {result.returncode}.")
    except Exception as e:
        print(f"\n[{datetime.now(IST).strftime('%Y-%m-%d %H:%M:%S IST')}] Error running daily snapshot: {e}")
    
    print(f"{'='*60}\n")


def get_local_time_for_ist(ist_hour: int, ist_minute: int):
    """
    Convert IST time to local system time.
    Returns (hour, minute) in local timezone.
    """
    # Get current date
    today = datetime.now().date()
    
    # Create target time in IST
    target_ist = IST.localize(datetime.combine(today, dtime(ist_hour, ist_minute)))
    
    # Get local timezone
    local_tz = datetime.now().astimezone().tzinfo
    
    # Convert IST time to local time
    target_local = target_ist.astimezone(local_tz)
    
    return target_local.hour, target_local.minute


def main():
    """Main scheduler function."""
    print("="*60)
    print("Daily Snapshot Scheduler")
    print("="*60)
    print(f"Script to run: {SCRIPT_PATH}")
    
    # Get current times
    now_local = datetime.now()
    now_ist = datetime.now(IST)
    
    print(f"Current local time: {now_local.strftime('%Y-%m-%d %H:%M:%S')} ({now_local.astimezone().tzname()})")
    print(f"Current IST time: {now_ist.strftime('%Y-%m-%d %H:%M:%S IST')}")
    print("="*60)
    print("\nScheduled times (IST):")
    print("  - 09:20 AM IST")
    print("  - 03:20 PM IST (15:20 IST)")
    
    # Convert IST times to local time
    local_0920_hour, local_0920_min = get_local_time_for_ist(9, 20)
    local_1520_hour, local_1520_min = get_local_time_for_ist(15, 20)
    
    print(f"\nScheduled times (Local):")
    print(f"  - {local_0920_hour:02d}:{local_0920_min:02d} (equivalent to 09:20 IST)")
    print(f"  - {local_1520_hour:02d}:{local_1520_min:02d} (equivalent to 15:20 IST)")
    print("\nScheduler is running... Press Ctrl+C to stop.\n")
    
    # Schedule at converted local times
    schedule.every().day.at(f"{local_0920_hour:02d}:{local_0920_min:02d}").do(run_daily_snapshot)
    schedule.every().day.at(f"{local_1520_hour:02d}:{local_1520_min:02d}").do(run_daily_snapshot)
    
    # Run scheduler loop
    while True:
        schedule.run_pending()
        time.sleep(60)  # Check every minute


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\nScheduler stopped by user.")
        sys.exit(0)

