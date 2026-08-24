#!/bin/bash
# Deploy Battery Guard server on Ubuntu 22.04+ VPS
# Run as root: bash deploy.sh

set -e

echo "=== Deploying Battery Guard Server ==="

# 1. Install dependencies
apt update && apt upgrade -y
apt install -y python3 python3-pip python3-venv postgresql nginx ufw

# 2. Configure firewall
ufw allow OpenSSH
ufw allow 'Nginx Full'
ufw --force enable

# 3. Setup PostgreSQL database and user
DB_PASS=$(openssl rand -hex 16)
API_SECRET=$(openssl rand -hex 24)
DASH_PASS=$(openssl rand -hex 12)
FLASK_SECRET=$(openssl rand -hex 24)

sudo -u postgres psql -c "CREATE USER monitor WITH PASSWORD '$DB_PASS';" || true
sudo -u postgres psql -c "CREATE DATABASE phone_monitor OWNER monitor;" || true

# 4. Setup app directory
mkdir -p /opt/batteryguard
cp -r ./* /opt/batteryguard/
cd /opt/batteryguard

# 5. Python virtual environment
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# 6. Create production .env
cat > .env << EOF
DATABASE_URL=postgresql://monitor:${DB_PASS}@localhost:5432/phone_monitor
API_KEY=${API_SECRET}
DASHBOARD_USERNAME=admin
DASHBOARD_PASSWORD=${DASH_PASS}
SECRET_KEY=${FLASK_SECRET}
EOF

# 7. Create systemd service
cat > /etc/systemd/system/batteryguard.service << 'EOF'
[Unit]
Description=Battery Guard Server
After=network.target postgresql.service

[Service]
User=www-data
WorkingDirectory=/opt/batteryguard
ExecStart=/opt/batteryguard/venv/bin/gunicorn -w 4 -b 127.0.0.1:5000 app:app
Restart=always
EnvironmentFile=/opt/batteryguard/.env

[Install]
WantedBy=multi-user.target
EOF

chown -R www-data:www-data /opt/batteryguard
systemctl daemon-reload
systemctl enable batteryguard
systemctl restart batteryguard

# 8. Configure Nginx
cat > /etc/nginx/sites-available/batteryguard << 'EOF'
server {
    listen 80;
    server_name _;

    location / {
        proxy_pass http://127.0.0.1:5000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        client_max_body_size 50M;
    }
}
EOF

ln -sf /etc/nginx/sites-available/batteryguard /etc/nginx/sites-enabled/
rm -f /etc/nginx/sites-enabled/default
nginx -t && systemctl restart nginx

echo "================================================="
echo "✅ Battery Guard Server Deployed Successfully!"
echo "-------------------------------------------------"
echo "Dashboard URL:      http://<SERVER_IP>/login"
echo "Username:          admin"
echo "Password:          ${DASH_PASS}"
echo "API Key for Phone: ${API_SECRET}"
echo "================================================="
