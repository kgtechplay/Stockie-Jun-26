# setup_scheduler.py
"""
Setup script to automatically start the daily snapshot scheduler using Windows Task Scheduler.
This will create a Windows scheduled task that runs the scheduler at system startup.
"""

import sys
import subprocess
from pathlib import Path
import os

# Get project root
project_root = Path(__file__).parent.parent
scheduler_script = project_root / "scripts" / "schedule_daily_snapshots.py"
python_exe = sys.executable

def create_scheduled_task():
    """Create a Windows Task Scheduler task to run the scheduler at startup."""
    
    # Task name
    task_name = "OT-v1_DailySnapshotScheduler"
    
    # Command to run
    command = f'"{python_exe}" "{scheduler_script}"'
    
    # Working directory
    working_dir = str(project_root)
    
    print("="*60)
    print("Setting up Daily Snapshot Scheduler")
    print("="*60)
    print(f"Task Name: {task_name}")
    print(f"Python: {python_exe}")
    print(f"Scheduler Script: {scheduler_script}")
    print(f"Working Directory: {working_dir}")
    print("="*60)
    print("\nThis will create a Windows Task Scheduler task that:")
    print("  - Runs at system startup")
    print("  - Runs the scheduler script continuously")
    print("  - Executes daily_intraday_stock_option.py at 9:20 AM and 3:20 PM IST")
    print()
    
    # Check if task already exists
    check_cmd = f'schtasks /query /tn "{task_name}"'
    result = subprocess.run(check_cmd, shell=True, capture_output=True, text=True)
    
    if result.returncode == 0:
        print(f"⚠️  Task '{task_name}' already exists!")
        response = input("Do you want to delete and recreate it? (y/n): ")
        if response.lower() != 'y':
            print("Cancelled.")
            return
        
        # Delete existing task
        print(f"\nDeleting existing task...")
        delete_cmd = f'schtasks /delete /tn "{task_name}" /f'
        result = subprocess.run(delete_cmd, shell=True, capture_output=True, text=True)
        if result.returncode != 0:
            print(f"Error deleting task: {result.stderr}")
            return
        print("Task deleted.")
    
    # Create the task
    print(f"\nCreating scheduled task...")
    
    # Create task command
    # /sc ONSTART = run at startup
    # /ru SYSTEM = run as SYSTEM account (or use current user)
    # /rl HIGHEST = highest privileges
    # /tr = task to run
    # /f = force create
    
    create_cmd = [
        'schtasks', '/create',
        '/tn', task_name,
        '/sc', 'ONSTART',  # Run at startup
        '/ru', os.environ.get('USERNAME', 'SYSTEM'),  # Run as current user
        '/rl', 'HIGHEST',  # Highest privileges
        '/tr', command,
        '/f'  # Force create
    ]
    
    result = subprocess.run(create_cmd, capture_output=True, text=True)
    
    if result.returncode == 0:
        print("✅ Task created successfully!")
        print(f"\nTask '{task_name}' will now run automatically at system startup.")
        print("\nTo manage the task:")
        print(f"  - View: schtasks /query /tn \"{task_name}\"")
        print(f"  - Delete: schtasks /delete /tn \"{task_name}\" /f")
        print(f"  - Run now: schtasks /run /tn \"{task_name}\"")
        print("\nOr use Windows Task Scheduler GUI:")
        print("  - Press Win+R, type 'taskschd.msc', press Enter")
        print(f"  - Look for task: {task_name}")
    else:
        print(f"❌ Error creating task:")
        print(result.stderr)
        print("\nYou may need to run this script as Administrator.")
        print("Right-click and select 'Run as administrator'")


def delete_scheduled_task():
    """Delete the Windows Task Scheduler task."""
    task_name = "OT-v1_DailySnapshotScheduler"
    
    print(f"Deleting task '{task_name}'...")
    delete_cmd = f'schtasks /delete /tn "{task_name}" /f'
    result = subprocess.run(delete_cmd, shell=True, capture_output=True, text=True)
    
    if result.returncode == 0:
        print("✅ Task deleted successfully!")
    else:
        print(f"❌ Error deleting task:")
        print(result.stderr)


if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description="Setup or remove Windows Task Scheduler for daily snapshot scheduler")
    parser.add_argument(
        "--remove",
        action="store_true",
        help="Remove the scheduled task instead of creating it"
    )
    args = parser.parse_args()
    
    if args.remove:
        delete_scheduled_task()
    else:
        create_scheduled_task()

