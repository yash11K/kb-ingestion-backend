#!/bin/bash
# =============================================================================
# Update & Restart Script — AEM KB Ingestion Backend
# Pushes local changes, SSHes into EC2, pulls, and restarts the service.
# Usage: bash deploy/update_and_restart.sh
# =============================================================================
set -e

EC2_HOST="ec2-user@54.160.238.80"
APP_DIR="/home/ec2-user/kb-ingestion-backend"

# --- Local: push changes ---
echo ">>> Pushing local changes to origin..."
git add -A
git commit -m "deploy: update" --allow-empty
git push origin main

# --- Remote: pull, install, restart ---
echo ">>> Connecting to EC2 and deploying..."
ssh -o StrictHostKeyChecking=no "$EC2_HOST" bash -s <<REMOTE
set -e
cd "$APP_DIR"

echo ">>> Pulling latest changes..."
git pull origin main

echo ">>> Activating virtual environment..."
source venv/bin/activate

echo ">>> Installing/updating dependencies..."
pip install --upgrade pip
pip install -r requirements.txt

echo ">>> Restarting service..."
sudo systemctl daemon-reload
sudo systemctl restart kb-backend

echo ""
echo "============================================="
echo "  Update complete!"
echo "============================================="
echo "  Check status:  sudo systemctl status kb-backend"
echo "  View logs:     sudo journalctl -u kb-backend -f"
echo ""
REMOTE
