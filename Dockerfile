# ----------------------------
# Stage 1: Build Flutter web
# ----------------------------
FROM ghcr.io/cirruslabs/flutter:stable AS flutter_build
WORKDIR /src

COPY flutter_app/ ./flutter_app/
WORKDIR /src/flutter_app
RUN flutter config --enable-web
RUN flutter pub get
RUN flutter build web --release

# ----------------------------
# Stage 2: Python API runtime
# ----------------------------
FROM python:3.11-slim-bookworm

# ODBC deps
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
    curl gnupg ca-certificates apt-transport-https && \
    curl -fsSL https://packages.microsoft.com/keys/microsoft.asc | gpg --dearmor -o /usr/share/keyrings/microsoft-prod.gpg && \
    echo "deb [arch=amd64,arm64,armhf signed-by=/usr/share/keyrings/microsoft-prod.gpg] https://packages.microsoft.com/debian/12/prod bookworm main" \
      > /etc/apt/sources.list.d/mssql-release.list && \
    apt-get update && \
    ACCEPT_EULA=Y DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
    msodbcsql18 unixodbc-dev && \
    apt-get clean && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy backend
COPY . .

# Copy Flutter build into expected location
RUN mkdir -p /app/flutter_app/build
COPY --from=flutter_build /src/flutter_app/build/web /app/flutter_app/build/web

RUN mkdir -p predictions/output && chmod 755 predictions/output

# Bind to Railway PORT
CMD ["sh", "-c", "gunicorn api:app --bind 0.0.0.0:${PORT:-8080} --workers 2 --threads 4 --timeout 120"]
    