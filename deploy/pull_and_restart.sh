#!/bin/bash
set -e

APP_DIR="/home/ec2-user/kb-ingestion-backend"
cd "$APP_DIR"

echo ">>> Pulling latest..."
git pull origin main

echo ">>> Installing deps..."
source venv/bin/activate
pip install -r requirements.txt

echo ">>> Restarting service..."
sudo systemctl daemon-reload
sudo systemctl restart kb-backend

echo ">>> Done! Status:"
sudo systemctl status kb-backend --no-pager
