#!/bin/bash
set -e

# Install ODBC driver dependencies
apt-get update
apt-get install -y curl gnupg

# Add Microsoft repository
curl https://packages.microsoft.com/keys/microsoft.asc | gpg --dearmor -o /usr/share/keyrings/microsoft-prod.gpg
curl https://packages.microsoft.com/config/debian/11/prod.list > /etc/apt/sources.list.d/mssql-release.list

# Install Microsoft ODBC Driver for SQL Server
apt-get update
ACCEPT_EULA=Y apt-get install -y msodbcsql18
apt-get install -y unixodbc-dev

# Install Python dependencies
pip install -r requirements.txt

