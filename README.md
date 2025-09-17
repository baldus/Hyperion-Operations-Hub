[README.md](https://github.com/user-attachments/files/22282410/README.md)
# 📦 invapp2 — Inventory Management System  

**invapp2** is a lightweight inventory management system built with **Flask**, designed to run on a **Raspberry Pi**.  
It supports tracking items, locations, stock balances, receiving, cycle counts, stock adjustments, transfers, and full transaction history.  

---

## 🚀 Features  

- **Item Management**  
  - Add, edit, import, and export items (CSV).  
  - Define min stock levels per SKU.  

- **Location Management**  
  - Add, import, and export storage locations.  
  - Codes and descriptions for warehouse/bin tracking.  

- **Stock Management**  
  - View stock by SKU, batch, and location.  
  - Manual adjustments with audit trail.  
  - Bulk stock imports via CSV.  
  - Export stock balances.  

- **Receiving**  
  - Receive items into stock.  
  - Auto-generate unique lot/batch numbers (`SKU-YYMMDD-##`).  
  - Record PO numbers and receiving personnel.  

- **Cycle Counts**  
  - Log physical counts vs. book stock.  
  - Movement types: `CYCLE_COUNT_CONFIRM` and `CYCLE_COUNT_ADJUSTMENT`.  
  - Export cycle counts to CSV.  

- **Transfers (Move)**  
  - Move stock between locations while preserving lot/batch tracking.  

- **Transaction History**  
  - Full log of all stock movements.  
  - Exportable to CSV.  
  - Color-coded and striped UI for readability.  

- **Reports**  
  - One-click export of all tables (Items, Locations, Batches, Movements) into a single ZIP.  

---

## 🛠 Tech Stack  

- **Backend**: Flask, SQLAlchemy  
- **Database**: PostgreSQL  
- **Frontend**: Jinja2 templates (dark theme, bubble-style buttons)  
- **Platform**: Raspberry Pi (Linux)  

---

## 📂 Project Structure  

```
invapp2/
│
├── app.py                # Application entry point
├── invapp/
│   ├── __init__.py       # App factory + blueprint registration
│   ├── models.py         # Database models
│   ├── routes/
│   │   ├── inventory.py  # Inventory module
│   │   ├── reports.py    # Reports module
│   │   └── orders.py     # Orders (planned)
│   └── templates/
│       ├── inventory/    # Inventory HTML templates
│       ├── reports/      # Reports HTML templates
│       └── orders/       # Orders (planned)
```

---

## ⚡ Setup Instructions  

1. Clone the repo:  
   ```bash
   git clone https://github.com/YOUR-USERNAME/invapp2.git
   cd invapp2
   ```

2. Create a virtual environment & install Python dependencies from `invapp2/requirements.txt`:
   ```bash
   python3 -m venv .venv
   source .venv/bin/activate
   # upgrade packaging tools on fresh environments
   pip install --upgrade pip setuptools wheel
   pip install -r requirements.txt
   ```

   > **Note:** The app expects PostgreSQL connectivity via `psycopg2-binary`. On Debian/Raspberry Pi systems ensure the `libpq` client libraries are installed (e.g. `sudo apt install libpq5`).

3. Set up your database and environment variables (see [`config.py`](invapp2/config.py) for all available options):
   ```bash
   export DB_URL="postgresql+psycopg2://USER:PASSWORD@localhost/invdb"
   export SECRET_KEY="change_me"
   export ZEBRA_PRINTER_HOST="printer.local"  # network address of your Zebra printer
   export ZEBRA_PRINTER_PORT=9100              # port for the printer connection
   flask shell
   >>> from invapp.extensions import db
   >>> db.create_all()
   ```

4. Run the app:
   ```bash
   flask run --host=0.0.0.0 --port=5000
   ```

5. Access via browser:
   ```
   http://<raspberry-pi-ip>:5000
   ```

6. (Optional) Run the automated tests once the app dependencies are installed:
   ```bash
   pytest
   ```

---

## 🗺 Roadmap  

- [x] Inventory module (MVP complete)  
- [x] Reports module (ZIP export)  
- [ ] Orders module (BOMs, routing, reservations)  
- [ ] User authentication & admin roles  
- [ ] More advanced reporting  

---

## 🤝 Contributing  

Contributions are welcome!  
- Fork the repo  
- Create a new branch (`git checkout -b feature/your-feature`)  
- Commit changes (`git commit -m "Add feature"`)  
- Push branch (`git push origin feature/your-feature`)  
- Open a Pull Request  

---

## 📜 License  

This project is licensed under the MIT License — see the [LICENSE](LICENSE) file for details.  
