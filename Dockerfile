FROM python:3.11-slim

# Install system dependencies for ODBC
# Install Microsoft ODBC Driver 18 for SQL Server
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
    curl \
    gnupg \
    ca-certificates \
    apt-transport-https \
    && curl -fsSL https://packages.microsoft.com/keys/microsoft.asc | gpg --dearmor -o /usr/share/keyrings/microsoft-prod.gpg \
    && bash -c 'DEB_VERSION=$(cat /etc/debian_version | cut -d. -f1); \
    if [ "$DEB_VERSION" = "12" ]; then \
        curl -fsSL https://packages.microsoft.com/config/debian/12/prod.list > /etc/apt/sources.list.d/mssql-release.list; \
    else \
        curl -fsSL https://packages.microsoft.com/config/debian/11/prod.list > /etc/apt/sources.list.d/mssql-release.list; \
    fi' && \
    apt-get update && \
    ACCEPT_EULA=Y DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
    msodbcsql18 \
    unixodbc-dev \
    && apt-get clean && \
    rm -rf /var/lib/apt/lists/*

# Set working directory
WORKDIR /app

# Copy requirements and install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Expose port
EXPOSE 8080

# Run gunicorn
CMD ["gunicorn", "api:app", "--bind", "0.0.0.0:8080"]


