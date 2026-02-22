# api.py
"""
Flask REST API backend for the Options Trading application.
"""
import os
import sys
from pathlib import Path
from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS

# Add project root to Python path
# This assumes api.py is in the project root, and src is a subdirectory
project_root = Path(__file__).parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from src.config import get_settings
from src.db_client import AzureSqlClient
from src.options_service import process_underlying_once
from src.option_fetcher import _normalize_underlying
from src.trend_service import fetch_option_trend_data
from src.prediction.prediction_service import PredictionService
from src.prediction.technical.strategies import PREDICTION_STRATEGIES

app = Flask(__name__)
CORS(app)  # Enable CORS for Flutter app

# Load settings lazily to avoid startup errors
settings = None

def get_settings_safe():
    """Get settings, initializing if needed."""
    global settings
    if settings is None:
        settings = get_settings()
    return settings


@app.route('/api/health', methods=['GET'])
def health_check():
    """Health check endpoint - should work even if other services fail."""
    try:
        return jsonify({
            "status": "ok",
            "message": "Backend is running"
        }), 200
    except Exception as e:
        return jsonify({
            "status": "error",
            "message": str(e)
        }), 500


@app.route('/api/stocks/count', methods=['GET'])
def get_stock_count():
    """Debug endpoint to check total stock count in database."""
    try:
        settings = get_settings_safe()
        db = AzureSqlClient(settings)
        db.connect()
        count = db.get_stock_count()
        db.close()
        return jsonify({"total_count": count}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/api/stocks/search', methods=['POST'])
def search_stocks():
    """
    Search for stocks by name, optionally filtered by segment.
    Request body: {"query": "Reliance", "segment": "NSE"}  # segment is optional
    Response: {"matches": [{"tradingsymbol": "...", "name": "...", "exchange": "..."}, ...]}
    """
    try:
        data = request.get_json()
        query = data.get('query', '').strip()
        segment = data.get('segment', '').strip() or None  # Optional segment filter
        
        if not query:
            return jsonify({"error": "Query parameter is required"}), 400
        
        settings = get_settings_safe()
        db = AzureSqlClient(settings)
        db.connect()
        
        # No limit - return all matching results
        matches = db.search_stocks_by_name(query, limit=None, segment=segment)
        db.close()
        
        matches_data = [
            {
                "tradingsymbol": s.tradingsymbol,
                "name": s.name,
                "exchange": s.exchange,
                "segment": s.segment,
            }
            for s in matches
        ]
        
        return jsonify({"matches": matches_data}), 200
        
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ----------------- Options: REFRESH (Kite -> DB) -----------------


@app.route("/api/options/process", methods=["POST"])
def process_options():
    """
    Refresh options data for a selected underlying.

    Request body:
      {"tradingsymbol": "NIFTY"} or {"tradingsymbol": "RELIANCE"}

    Behavior:
      - Fetch latest NFO instruments from Kite
      - Filter for underlying
      - Upsert OptionInstrument
      - Fetch quotes + IV/Greeks
      - Insert into OptionSnapshot + OptionSnapshotCalc
    """
    import logging
    import traceback

    logging.basicConfig(level=logging.INFO)
    logger = logging.getLogger(__name__)

    try:
        logger.info("Received /api/options/process request")
        data = request.get_json()

        if not data:
            logger.error("No JSON data in request")
            return jsonify({"error": "Request body is required", "success": False}), 400

        tradingsymbol_raw = (data.get("tradingsymbol") or "").strip()
        if not tradingsymbol_raw:
            logger.error("Missing tradingsymbol in request")
            return jsonify(
                {"error": "tradingsymbol is required", "success": False}
            ), 400

        # Normalize the underlying name (e.g., "NIFTY50" -> "NIFTY")
        normalized_underlying = _normalize_underlying(tradingsymbol_raw)
        if not normalized_underlying:
            logger.error(f"Invalid tradingsymbol: {tradingsymbol_raw}")
            return jsonify(
                {"error": "Invalid tradingsymbol", "success": False}
            ), 400

        logger.info(f"Starting options processing for {tradingsymbol_raw} (normalized: {normalized_underlying})")

        try:
            settings = get_settings_safe()
        except Exception as e:
            logger.error(f"Failed to get settings: {e}")
            return jsonify(
                {
                    "error": f"Configuration error: {str(e)}",
                    "success": False,
                }
            ), 500

        logger.info(f"Fetching and processing options for {normalized_underlying}...")

        try:
            contracts, snapshots = process_underlying_once(normalized_underlying, settings)
            logger.info(f"Completed: {contracts} contracts, {snapshots} snapshots")

            return jsonify(
                {
                    "success": True,
                    "message": (
                        f"Processed {contracts} option contracts and "
                        f"inserted {snapshots} snapshots for {normalized_underlying}"
                    ),
                    "option_count": contracts,
                    "snapshot_count": snapshots,
                    "underlying_symbol": normalized_underlying,
                    "original_input": tradingsymbol_raw,
                }
            ), 200
        except Exception as proc_error:
            logger.error(f"Error in process_underlying_once: {proc_error}")
            traceback.print_exc()
            return jsonify(
                {
                    "error": f"Processing error: {str(proc_error)}",
                    "success": False,
                    "message": f"Error processing options: {str(proc_error)}",
                }
            ), 500

    except Exception as e:
        import traceback

        error_msg = str(e)
        logger.error(f"Unexpected error in process_options: {error_msg}")
        traceback.print_exc()
        return jsonify(
            {
                "error": error_msg,
                "success": False,
                "message": f"Unexpected error: {error_msg}",
            }
        ), 500

# ----------------- Options: VIEW latest chain (DB only) -----------------


@app.route("/api/options/latest", methods=["GET"])
def get_latest_options():
    """
    View latest option chain for a given underlying, from DB only.

    Request:
      GET /api/options/latest?tradingsymbol=NIFTY

    Response:
      {
        "underlying": "NIFTY",
        "count": 123,
        "rows": [
          {
            "option_instrument_id": ...,
            "underlying": "...",
            "tradingsymbol": "...",
            "strike": ...,
            "expiry": "...",
            "instrument_type": "CE"/"PE",
            "snapshot_time": "...",
            "underlying_price": ...,
            "last_price": ...,
            "bid_price": ...,
            "bid_qty": ...,
            "ask_price": ...,
            "ask_qty": ...,
            "volume": ...,
            "open_interest": ...,
            "implied_volatility": ...,
            "delta": ...,
            "gamma": ...,
            "theta": ...,
            "vega": ...
          },
          ...
        ]
      }
    """
    try:
        tradingsymbol_raw = (request.args.get("tradingsymbol") or "").strip()
        if not tradingsymbol_raw:
            return (
                jsonify(
                    {"error": "tradingsymbol query param is required", "success": False}
                ),
                400,
            )

        # Normalize the underlying name (e.g., "NIFTY50" -> "NIFTY")
        normalized_underlying = _normalize_underlying(tradingsymbol_raw)
        if not normalized_underlying:
            return (
                jsonify(
                    {"error": "Invalid tradingsymbol", "success": False}
                ),
                400,
            )

        settings = get_settings_safe()
        db = AzureSqlClient(settings)
        db.connect()
        rows = db.fetch_latest_option_chain_for_underlying(normalized_underlying)
        db.close()

        return (
            jsonify(
                {
                    "success": True,
                    "underlying": normalized_underlying,
                    "original_input": tradingsymbol_raw,
                    "count": len(rows),
                    "rows": rows,
                }
            ),
            200,
        )

    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


# ----------------- Options: HISTORICAL TREND -----------------


@app.route("/api/options/trend", methods=["GET"])
def get_option_trend():
    """
    Get historical trend data for a specific option instrument.
    
    Request:
      GET /api/options/trend?option_instrument_id=123&days=30
    
    Response:
      {
        "success": true,
        "option_instrument_id": 123,
        "tradingsymbol": "NIFTY25DEC24000CE",
        "strike": 24000.0,
        "expiry": "2024-12-25",
        "instrument_type": "CE",
        "data_points": [
          {
            "date": "2024-11-01",
            "timestamp": "2024-11-01T10:00:00",
            "underlying_price": 24050.0,
            "option_price": 150.5,
            "implied_volatility": 0.18,
            "delta": 0.45,
            "gamma": 0.001,
            "theta": -5.2,
            "vega": 12.5
          },
          ...
        ]
      }
    """
    try:
        option_instrument_id = request.args.get("option_instrument_id")
        days = request.args.get("days", "30")
        
        if not option_instrument_id:
            return jsonify({
                "success": False,
                "error": "option_instrument_id query param is required"
            }), 400
        
        try:
            option_instrument_id = int(option_instrument_id)
            days = int(days)
        except ValueError:
            return jsonify({
                "success": False,
                "error": "option_instrument_id and days must be integers"
            }), 400
        
        settings = get_settings_safe()
        trend_data = fetch_option_trend_data(
            option_instrument_id=option_instrument_id,
            days=days,
            settings=settings,
        )
        
        return jsonify({
            "success": True,
            **trend_data,
        }), 200
        
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({
            "success": False,
            "error": str(e)
        }), 500


# ----------------- Predictions: INDEX PREDICTION & BACKTEST -----------------

@app.route('/api/predictions/strategies', methods=['GET'])
def get_prediction_strategies():
    """
    Get list of all available prediction strategies.
    
    Response:
    {
        "success": true,
        "strategies": ["trendUpRangeBreakout", "MaTrend_001", ...]
    }
    """
    try:
        strategies = list(PREDICTION_STRATEGIES.keys())
        return jsonify({
            "success": True,
            "strategies": strategies
        }), 200
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({
            "success": False,
            "error": str(e)
        }), 500


@app.route('/api/predictions/run', methods=['POST'])
def run_predictions():
    """
    Run prediction generation for selected instrument and strategies.
    
    Request body:
    {
        "instrument": "NIFTY" or "BANKNIFTY",
        "strategies": ["trendUpRangeBreakout", "MaTrend_001", ...],
        "use_agentic": true  # optional, defaults to USE_AGENTIC_AGGREGATOR env
    }
    
    Response:
    {
        "success": true,
        "message": "Predictions generated successfully",
        "files": ["NIFTY_trendUpRangeBreakout_predicted.csv", ...]
    }
    """
    import logging
    
    logging.basicConfig(level=logging.INFO)
    logger = logging.getLogger(__name__)
    
    try:
        data = request.get_json()
        if not data:
            return jsonify({"success": False, "error": "Request body is required"}), 400
        
        instrument = data.get("instrument", "").strip().upper()
        strategies = data.get("strategies", [])
        
        if instrument not in ["NIFTY", "BANKNIFTY"]:
            return jsonify({"success": False, "error": "instrument must be NIFTY or BANKNIFTY"}), 400
        
        if not strategies or not isinstance(strategies, list):
            return jsonify({"success": False, "error": "strategies must be a non-empty list"}), 400
        
        project_root = Path(__file__).parent
        service = PredictionService.from_project_root(project_root)
        use_agentic_raw = data.get("use_agentic")
        if use_agentic_raw is None:
            use_agentic = os.getenv("USE_AGENTIC_AGGREGATOR", "0") == "1"
        else:
            if isinstance(use_agentic_raw, bool):
                use_agentic = use_agentic_raw
            else:
                use_agentic = str(use_agentic_raw).strip().lower() in {"1", "true", "yes", "y"}
        
        # Clear existing output files for this instrument before generating new ones
        # This ensures backtest only compares files from the current run
        output_dir = project_root / "output"
        if output_dir.exists():
            import glob
            pattern = str(output_dir / f"{instrument}_*")
            existing_files = glob.glob(pattern)
            for file_path in existing_files:
                try:
                    os.remove(file_path)
                    logger.info(f"Cleared existing file: {os.path.basename(file_path)}")
                except Exception as e:
                    logger.warning(f"Could not remove {file_path}: {e}")
        
        generated_files = []
        errors = []
        
        for strategy in strategies:
            try:
                logger.info(
                    "Running prediction for %s with strategy %s (agentic=%s)",
                    instrument,
                    strategy,
                    use_agentic,
                )
                preds_df = service.generate_predictions_for_strategy(
                    instrument=instrument,
                    strategy=strategy,
                    use_agentic=use_agentic,
                )
                filename = service.save_predictions(
                    instrument=instrument,
                    strategy=strategy,
                    predictions_df=preds_df,
                )
                generated_files.append(filename)
                logger.info("Successfully generated %s", filename)
            except Exception as e:
                errors.append(f"{strategy}: {str(e)}")
                logger.error(f"Exception running {strategy}: {e}")
        
        if generated_files:
            return jsonify({
                "success": True,
                "message": f"Generated {len(generated_files)} prediction file(s)",
                "files": generated_files,
                "errors": errors if errors else None
            }), 200
        else:
            return jsonify({
                "success": False,
                "error": "No files generated",
                "errors": errors
            }), 500
            
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({
            "success": False,
            "error": str(e)
        }), 500


@app.route('/api/predictions/backtest', methods=['POST'])
def run_backtest():
    """
    Run index backtest for selected instrument.
    
    Request body:
    {
        "instrument": "NIFTY" or "BANKNIFTY"
    }
    
    Response:
    {
        "success": true,
        "message": "Backtest completed successfully",
        "comparison_file": "NIFTY_index_comparison.xlsx"
    }
    """
    import subprocess
    import logging
    
    logging.basicConfig(level=logging.INFO)
    logger = logging.getLogger(__name__)
    
    try:
        data = request.get_json()
        if not data:
            return jsonify({"success": False, "error": "Request body is required"}), 400
        
        instrument = data.get("instrument", "").strip().upper()
        
        if instrument not in ["NIFTY", "BANKNIFTY"]:
            return jsonify({"success": False, "error": "instrument must be NIFTY or BANKNIFTY"}), 400
        
        project_root = Path(__file__).parent
        backtest_script = project_root / "src" / "backtest" / "index_backtest.py"
        
        if not backtest_script.exists():
            return jsonify({"success": False, "error": "src/backtest/index_backtest.py not found"}), 500
        
        # Clear existing comparison file for this instrument before running backtest
        # This ensures we always have the latest comparison results
        output_dir = project_root / "output"
        comparison_file = output_dir / f"{instrument}_index_comparison.xlsx"
        if comparison_file.exists():
            try:
                os.remove(comparison_file)
                logger.info(f"Cleared existing comparison file: {comparison_file.name}")
            except Exception as e:
                logger.warning(f"Could not remove comparison file: {e}")
        
        logger.info(f"Running backtest for {instrument}")
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "src.backtest.index_backtest",
                "-u", instrument
            ],
            cwd=str(project_root),
            capture_output=True,
            text=True,
            timeout=600  # 10 minute timeout
        )
        
        if result.returncode == 0:
            comparison_file = f"{instrument}_index_comparison.xlsx"
            return jsonify({
                "success": True,
                "message": "Backtest completed successfully",
                "comparison_file": comparison_file
            }), 200
        else:
            error_msg = result.stderr or result.stdout
            logger.error(f"Backtest error: {error_msg}")
            return jsonify({
                "success": False,
                "error": f"Backtest failed: {error_msg[:500]}"
            }), 500
            
    except subprocess.TimeoutExpired:
        return jsonify({
            "success": False,
            "error": "Backtest timeout after 10 minutes"
        }), 500
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({
            "success": False,
            "error": str(e)
        }), 500


@app.route('/api/predictions/files', methods=['GET'])
def list_prediction_files():
    """
    List all generated files in the output folder.
    
    Query params:
    - instrument (optional): Filter by NIFTY or BANKNIFTY
    
    Response:
    {
        "success": true,
        "files": [
            {
                "name": "NIFTY_trendUpRangeBreakout_predicted.csv",
                "type": "prediction",
                "url": "/api/predictions/files/download?file=NIFTY_trendUpRangeBreakout_predicted.csv"
            },
            ...
        ]
    }
    """
    try:
        project_root = Path(__file__).parent
        output_dir = project_root / "output"
        
        if not output_dir.exists():
            return jsonify({
                "success": True,
                "files": []
            }), 200
        
        instrument_filter = request.args.get("instrument", "").strip().upper()
        
        files = []
        for file_path in output_dir.iterdir():
            if file_path.is_file():
                filename = file_path.name
                
                # Filter by instrument if specified
                if instrument_filter and instrument_filter not in filename.upper():
                    continue
                
                # Determine file type
                file_type = "unknown"
                if "_predicted.csv" in filename:
                    file_type = "prediction"
                elif "_index_comparison.xlsx" in filename:
                    file_type = "comparison"
                elif filename.endswith(".csv"):
                    file_type = "csv"
                elif filename.endswith(".xlsx"):
                    file_type = "excel"
                
                files.append({
                    "name": filename,
                    "type": file_type,
                    "size": file_path.stat().st_size,
                    "url": f"/api/predictions/files/download?file={filename}"
                })
        
        # Sort by name
        files.sort(key=lambda x: x["name"])
        
        return jsonify({
            "success": True,
            "files": files
        }), 200
        
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({
            "success": False,
            "error": str(e)
        }), 500


@app.route('/api/predictions/files/download', methods=['GET'])
def download_prediction_file():
    """
    Download a generated prediction file.
    
    Query params:
    - file: Filename to download (e.g., "NIFTY_trendUpRangeBreakout_predicted.csv")
    
    Returns the file for download.
    """
    from flask import send_file
    
    try:
        filename = request.args.get("file", "").strip()
        if not filename:
            return jsonify({"success": False, "error": "file parameter is required"}), 400
        
        # Security: prevent directory traversal
        if ".." in filename or "/" in filename or "\\" in filename:
            return jsonify({"success": False, "error": "Invalid filename"}), 400
        
        project_root = Path(__file__).parent
        file_path = project_root / "output" / filename
        
        if not file_path.exists() or not file_path.is_file():
            return jsonify({"success": False, "error": "File not found"}), 404
        
        return send_file(
            str(file_path),
            as_attachment=True,
            download_name=filename
        )
        
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({
            "success": False,
            "error": str(e)
        }), 500


# ----------------- Serve Flutter Web App -----------------

from werkzeug.exceptions import NotFound

def _flutter_build_dir() -> Path:
    """
    Where Flutter web build is expected inside the container.
    Your Docker build should ensure this path exists.
    """
    return project_root / "flutter_app" / "build" / "web"


@app.route("/", defaults={"path": ""}, methods=["GET"])
@app.route("/<path:path>", methods=["GET"])
def serve_flutter(path: str):
    """
    Serve Flutter web app static files from flutter_app/build/web.

    Rules:
    - Never handle /api/* here (let Flask API routes do that)
    - Serve real files if they exist (assets, flutter.js, etc.)
    - Otherwise serve index.html (SPA routing)
    """
    # 1) Don't hijack API routes
    if path.startswith("api/") or path == "api":
        raise NotFound()

    build_dir = _flutter_build_dir()
    index_file = build_dir / "index.html"

    # 2) If Flutter build isn't present, show helpful message
    if not index_file.exists():
        return jsonify({
            "error": "Flutter web build not found in container",
            "expected_path": str(build_dir),
            "fix": "Build Flutter web during Docker build (recommended) or run 'flutter build web' and commit build/web (not recommended).",
            "api_health": "/api/health"
        }), 404

    # 3) Serve static files if they exist (e.g., assets/, main.dart.js, flutter.js)
    if path:
        requested = build_dir / path
        if requested.exists() and requested.is_file():
            return send_from_directory(str(build_dir), path)

        # If path is a directory (rare), try index.html inside it
        if requested.exists() and requested.is_dir():
            dir_index = requested / "index.html"
            if dir_index.exists():
                return send_from_directory(str(requested), "index.html")

    # 4) SPA fallback
    return send_from_directory(str(build_dir), "index.html")


if __name__ == "__main__":
    # Local dev only (Railway uses gunicorn)
    port = int(os.environ.get("PORT", "5000"))
    app.run(host="0.0.0.0", port=port, debug=True, threaded=True)
