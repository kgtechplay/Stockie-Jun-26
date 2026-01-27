# Local Testing Guide

This guide explains how to test the Flutter app with the Flask API backend locally.

## Prerequisites

1. **Python 3.11+** with pip
2. **Flutter SDK** installed and configured
3. **Database connection** configured (check your `.env` file)

## Step 1: Set Up Python Backend

1. **Install Python dependencies:**
   ```bash
   pip install -r requirements.txt
   ```

2. **Start the Flask API server:**
   ```bash
   python run_local.py
   ```
   
   The API will start on `http://localhost:5000`
   
   You should see:
   ```
   ==================================================
   Starting Options Trading API Backend
   ==================================================
   Server will run at: http://localhost:5000
   API Health Check: http://localhost:5000/api/health
   ==================================================
   ```

3. **Verify the API is running:**
   - Open `http://localhost:5000/api/health` in your browser
   - You should see: `{"status":"ok","message":"Backend is running"}`

## Step 2: Set Up Flutter App

1. **Navigate to the Flutter app directory:**
   ```bash
   cd flutter_app
   ```

2. **Install Flutter dependencies:**
   ```bash
   flutter pub get
   ```

3. **Run the Flutter web app:**
   ```bash
   flutter run -d chrome
   ```
   
   Or for a specific port:
   ```bash
   flutter run -d chrome --web-port=8080
   ```

## Step 3: Test the Predictions API

The Flutter app is already configured to automatically detect localhost and connect to `http://localhost:5000/api`.

1. **Access the Prediction Test Screen:**
   - The app should open in your browser
   - Navigate to the prediction test screen (if available in your UI)

2. **Test the API endpoints:**
   - The app will automatically use `http://localhost:5000/api` when running on localhost
   - You can test:
     - `/api/predictions/strategies` - Get list of available strategies
     - `/api/predictions/run` - Run predictions for selected strategies
     - `/api/predictions/files` - List generated prediction files

## Testing the Predictions Endpoint

You can test the predictions API directly using curl or Postman:

```bash
# Get available strategies
curl http://localhost:5000/api/predictions/strategies

# Run predictions for specific strategies
curl -X POST http://localhost:5000/api/predictions/run \
  -H "Content-Type: application/json" \
  -d '{
    "instrument": "NIFTY",
    "strategies": ["MaTrend_001", "MaTrend_0005"]
  }'
```

## Troubleshooting

### CORS Issues
- The Flask app already has CORS enabled via `flask-cors`
- If you encounter CORS errors, check that `CORS(app)` is in `api.py`

### Port Conflicts
- If port 5000 is in use, modify `run_local.py` to use a different port
- Update the Flutter app's `getApiBaseUrl()` function in `main.dart` to match

### Database Connection
- Ensure your `.env` file has correct database credentials
- The API needs database access to fetch index data

### Flutter Build Issues
- Run `flutter clean` and `flutter pub get` if you encounter build errors
- Ensure you have the latest Flutter SDK

## Notes

- The Flutter app automatically detects localhost and uses `http://localhost:5000/api`
- For production builds, it uses relative URLs (`/api`)
- The API runs in debug mode locally, so you'll see detailed error messages
- Changes to `api.py` will require restarting the Flask server
- Changes to Flutter code will hot-reload automatically

