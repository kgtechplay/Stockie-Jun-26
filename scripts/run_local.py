#!/usr/bin/env python3
"""
Local development server startup script.
Run this to start the Flask API backend locally.
"""
import sys
from pathlib import Path

# Add project root to Python path
project_root = Path(__file__).parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from api import app

if __name__ == '__main__':
    import os
    port = int(os.environ.get("PORT", "5000"))

    print("=" * 50)
    print("Starting Options Trading API Backend")
    print("=" * 50)
    print(f"Server will run at: http://localhost:{port}")
    print(f"API Health Check: http://localhost:{port}/api/health")
    print("=" * 50)
    print("Press Ctrl+C to stop the server")
    print("=" * 50)
    print()
    
    # Run Flask development server
    # Allow overriding port via env var (useful if 5000 is already in use on Windows)
    app.run(host='0.0.0.0', port=port, debug=True)


