# 🚀 Deployment Guide

This guide walks through provisioning a production-ready instance of the
Hyperion Operations Hub on Raspberry Pi OS Lite or Debian/Ubuntu systems. The
steps assume a fresh 64-bit image with SSH access.

---

## 1. Prepare the Operating System

1. **Flash the OS** – Use Raspberry Pi Imager (or Balena Etcher) to flash Raspberry
   Pi OS Lite (64-bit) to an SSD or high-endurance microSD card.
2. **Enable Remote Access** – In Raspberry Pi Imager, enable SSH and set hostname
   + credentials, or place an empty `ssh` file on the boot partition.
3. **First Boot & Updates**
   ```bash
   sudo apt update
   sudo apt full-upgrade -y
   sudo reboot
   ```
4. **Install Base Tooling**
   ```bash
   sudo apt install -y git python3 python3-venv python3-pip libpq5 libpq-dev build-essential
   ```

---

## 2. System Configuration

- **Hostname** – Helps identify the node on the network.
  ```bash
  sudo hostnamectl set-hostname hyperion-ops
  ```
- **Timezone & Locale** – Match production facility settings.
  ```bash
  sudo timedatectl set-timezone YOUR/REGION
  sudo raspi-config nonint do_change_locale en_US.UTF-8
  ```
- **Networking** – Reserve a static IP or DHCP reservation so printers, barcode
  scanners, and administrators can reach the hub predictably.
- **Firewall (optional)** – Allow SSH and the gunicorn port (default 8000).
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

Create the database role and schema:

```bash
sudo -u postgres psql <<'SQL'
CREATE ROLE inv WITH LOGIN PASSWORD 'change_me';
CREATE DATABASE invdb OWNER inv;
GRANT ALL PRIVILEGES ON DATABASE invdb TO inv;
SQL
```

Adjust `/etc/postgresql/*/main/pg_hba.conf` to use `md5` or `scram-sha-256` for
local connections, then reload PostgreSQL:

```bash
sudo systemctl reload postgresql
```

Store credentials securely (password manager or sealed envelope).

---

## 4. Deploy the Application

1. **Clone the Repository** (under `/opt` or a dedicated service account):
   ```bash
   sudo mkdir -p /opt/hyperion
   sudo chown $USER:$USER /opt/hyperion
   git clone https://github.com/YOUR-ORG/Hyperion-Operations-Hub.git /opt/hyperion
   cd /opt/hyperion/invapp2
   ```
2. **Create Virtual Environment & Install Dependencies**
   ```bash
   python3 -m venv .venv
   source .venv/bin/activate
   pip install --upgrade pip setuptools wheel
   pip install -r requirements.txt
   ```
3. **Configure Environment Variables** – Create `/opt/hyperion/invapp2/.env`:
   ```bash
   DB_URL=postgresql+psycopg2://inv:change_me@localhost/invdb
   SECRET_KEY=super-secret
   ADMIN_USER=superuser
   ADMIN_PASSWORD=change_me
   ADMIN_SESSION_TIMEOUT=600
   ZEBRA_PRINTER_HOST=printer.local
   ZEBRA_PRINTER_PORT=9100
   ```
4. **Initialize the Database Schema**
   ```bash
   source .venv/bin/activate
   flask --app app shell <<'PYTHON'
   from invapp.extensions import db
   from invapp import create_app
   app = create_app()
   with app.app_context():
       db.create_all()
   PYTHON
   ```
5. **Smoke-Test the Application**
   ```bash
   gunicorn --bind 0.0.0.0:8000 app:app
   ```
   Visit `http://<host-ip>:8000`, log in with the admin credentials, and verify
   inventory and reporting pages load successfully.

---

## 5. Run as a Service

Create `/etc/systemd/system/hyperion.service`:

```ini
[Unit]
Description=Hyperion Operations Hub
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=/opt/hyperion/invapp2
EnvironmentFile=/opt/hyperion/invapp2/.env
ExecStart=/opt/hyperion/invapp2/.venv/bin/gunicorn --bind 0.0.0.0:8000 --workers 3 --timeout 120 app:app
Restart=on-failure
User=pi
Group=pi

[Install]
WantedBy=multi-user.target
```

Reload systemd and enable the service:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now hyperion.service
sudo systemctl status hyperion.service
```

For production hardening, consider placing Nginx or Caddy in front of gunicorn to
terminate HTTPS and provide request buffering.

---

## 6. Backups & Maintenance

- **Database Dumps** – Schedule nightly exports:
  ```bash
  sudo crontab -e
  # Example cron entry (adjust path and retention)
  0 2 * * * pg_dump invdb > /var/backups/invdb-$(date +\%F).sql
  ```
- **Log Rotation** – If you redirect gunicorn logs to files, configure `logrotate`
  for `/var/log/hyperion/*.log`.
- **System Updates** – Apply OS patches monthly and reboot after kernel updates.
- **Printer Checks** – Confirm Zebra printers have labels/ribbons and clear error
  states as part of daily shift handoff.

---

## 7. Scaling Strategies

- **External PostgreSQL** – Point `DB_URL` to a centralized PostgreSQL instance
  when multiple facilities need to share data.
- **Disaster Recovery** – Keep a cold spare Pi or VM image. Sync `/opt/hyperion`
  nightly via `rsync` and store database dumps off-device.
- **Monitoring & Alerts** – Integrate with Netdata, Prometheus, or lightweight
  uptime monitors. Alert on CPU, disk, gunicorn service status, and printer
  connectivity.

Following this playbook yields a maintainable, resilient deployment ready for
production workloads.
