#!/bin/bash
# =============================================================================
# EC2 Setup Script — AEM KB Ingestion Backend
# For Amazon Linux 2023 (ec2-user)
# Usage: bash setup_ec2.sh
# =============================================================================
set -e

APP_DIR="/home/ec2-user/kb-ingestion-backend"
REPO_URL="https://github.com/yash11K/kb-ingestion-backend.git"

echo ">>> Updating system packages..."
sudo dnf update -y

echo ">>> Installing Python 3.11, git, and dev tools..."
sudo dnf install -y python3.11 python3.11-pip python3.11-devel git

echo ">>> Cloning repository..."
if [ -d "$APP_DIR" ]; then
    echo "Directory exists, pulling latest..."
    cd "$APP_DIR"
    git pull origin main
else
    git clone "$REPO_URL" "$APP_DIR"
    cd "$APP_DIR"
fi

echo ">>> Creating virtual environment..."
python3.11 -m venv venv
source venv/bin/activate

echo ">>> Installing dependencies..."
pip install --upgrade pip
pip install -r requirements.txt

echo ">>> Creating .env file template (edit with real values)..."
if [ ! -f .env ]; then
    cp .env.example .env
    echo "*** IMPORTANT: Edit $APP_DIR/.env with your actual values ***"
else
    echo ".env already exists, skipping..."
fi

echo ">>> Setting up systemd service (port 80)..."
sudo tee /etc/systemd/system/kb-backend.service > /dev/null <<EOF
[Unit]
Description=AEM KB Ingestion Backend
After=network.target

[Service]
Type=simple
User=ec2-user
WorkingDirectory=$APP_DIR
EnvironmentFile=$APP_DIR/.env
ExecStart=$APP_DIR/venv/bin/uvicorn src.main:create_app --factory --host 0.0.0.0 --port 80
Restart=always
RestartSec=5
# Allow binding to port 80 without running as root
AmbientCapabilities=CAP_NET_BIND_SERVICE

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable kb-backend

echo ""
echo "============================================="
echo "  Setup complete!"
echo "============================================="
echo ""
echo "Next steps:"
echo "  1. Edit the .env file:  nano $APP_DIR/.env"
echo "  2. Start the service:   sudo systemctl start kb-backend"
echo "  3. Check status:        sudo systemctl status kb-backend"
echo "  4. View logs:           sudo journalctl -u kb-backend -f"
echo "  5. App will be at:      http://54.160.238.80"
echo ""
