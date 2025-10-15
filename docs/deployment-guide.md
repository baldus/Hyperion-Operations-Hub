# 🚀 Deployment Guide

This document walks through provisioning a production-ready instance of **invapp2** on a Raspberry Pi or similar Linux edge device. The steps assume a fresh Raspberry Pi OS Lite 64-bit image, but can be adapted for Debian/Ubuntu systems.

---

## 1. Prepare the Operating System

1. **Flash Raspberry Pi OS Lite (64-bit)** to SSD or high-endurance microSD.
2. **Enable SSH** by placing an empty file named `ssh` in the boot partition or by using Raspberry Pi Imager's advanced settings.
3. Boot the Pi, log in (`pi`/`raspberry` by default), and immediately change the password.
4. Update packages and firmware:
   ```bash
   sudo apt update
   sudo apt full-upgrade -y
   sudo reboot
   ```
5. After reboot, install base tooling:
   ```bash
   sudo apt install -y git python3 python3-venv python3-pip libpq5 libpq-dev build-essential
   ```

---

## 2. Configure Networking & System Settings

- **Static IP / DHCP reservation:** Assign a predictable address for printers and remote administration.
- **Hostname:**
  ```bash
  sudo hostnamectl set-hostname hyperion-ops
  ```
- **Timezone & locale:**
  ```bash
  sudo timedatectl set-timezone YOUR/REGION
  sudo raspi-config nonint do_change_locale en_US.UTF-8
  ```
- **Firewall (optional):**
  ```bash
  sudo apt install -y ufw
  sudo ufw allow 22/tcp
  sudo ufw allow 8000/tcp
  sudo ufw enable
  ```

---

## 3. Install PostgreSQL

```bash
sudo apt install -y postgresql postgresql-contrib
sudo systemctl enable --now postgresql
```

Create a database role and database:
```bash
sudo -u postgres psql <<'SQL'
CREATE ROLE inv WITH LOGIN PASSWORD 'change_me';
CREATE DATABASE invdb OWNER inv;
GRANT ALL PRIVILEGES ON DATABASE invdb TO inv;
SQL
```

Edit `/etc/postgresql/13/main/pg_hba.conf` (version may differ) to use `md5` or `scram-sha-256` for local connections, then reload PostgreSQL:
```bash
sudo systemctl reload postgresql
```

Back up the credentials in a secure location.

---

## 4. Deploy the Application

1. **Clone the repository** (place under `/opt` or a dedicated service account):
   ```bash
   sudo mkdir -p /opt/invapp2
   sudo chown $USER:$USER /opt/invapp2
   git clone https://github.com/YOUR-ORG/invapp2.git /opt/invapp2
   cd /opt/invapp2
   ```

2. **Create the virtual environment and install dependencies:**
   ```bash
   python3 -m venv .venv
   source .venv/bin/activate
   pip install --upgrade pip setuptools wheel
   pip install -r requirements.txt
   ```

3. **Configure environment variables:**
   Create `/opt/invapp2/.env` (if you use `direnv` or `systemd` EnvironmentFile) with values:
   ```bash
   DB_URL=postgresql+psycopg://inv:change_me@localhost/invdb
   SECRET_KEY=super-secret
   ADMIN_SESSION_TIMEOUT=600
   ZEBRA_PRINTER_HOST=printer.local
   ZEBRA_PRINTER_PORT=9100
   ```

4. **Initialize the database schema:**
   ```bash
   source .venv/bin/activate
   flask shell <<'PYTHON'
   from invapp.extensions import db
   db.create_all()
   PYTHON
   ```

5. **Smoke-test the application:**
   ```bash
   gunicorn --bind 0.0.0.0:8000 app:app
   ```
   Visit `http://<pi-ip>:8000` and verify that you can log in, view inventory, and access reports.

---

## 5. Run as a Service

Create a systemd unit so the app starts on boot. Save the following as `/etc/systemd/system/invapp2.service`:

```ini
[Unit]
Description=invapp2 Inventory Service
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=/opt/invapp2
EnvironmentFile=/opt/invapp2/.env
ExecStart=/opt/invapp2/.venv/bin/gunicorn --bind 0.0.0.0:8000 --workers 3 --timeout 120 app:app
Restart=on-failure
User=pi
Group=pi

[Install]
WantedBy=multi-user.target
```

Reload systemd and enable the service:
```bash
sudo systemctl daemon-reload
sudo systemctl enable --now invapp2.service
sudo systemctl status invapp2.service
```

For hardened deployments, consider placing Nginx or Caddy in front of Flask with HTTPS termination.

---

## 6. Backups & Maintenance

- **Database dumps:** Schedule a nightly cron job:
  ```bash
  sudo crontab -e
  # Add the line below (adjust paths)
  0 2 * * * pg_dump invdb > /var/backups/invdb-$(date +\%F).sql
  ```
- **Log rotation:** Use `logrotate` if you redirect Flask logs to files.
- **System updates:** Apply OS patches monthly. Reboot the Pi after kernel updates.
- **Printer checks:** Confirm Zebra printers have labels/ribbons and clear any error states daily.

---

## 7. Scaling Beyond a Single Pi

- **Separate PostgreSQL host:** Move the database to an x86 server or managed instance. Update `DB_URL` accordingly.
- **High availability:** Use two Pi devices—one primary, one cold spare synced via nightly backups.
- **Monitoring:** Integrate with Prometheus + Grafana or a lightweight uptime monitor to track service health.

Following these steps results in a maintainable production deployment with predictable behavior and recoverability.
