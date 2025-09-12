import os

class Config:
    SQLALCHEMY_DATABASE_URI = os.getenv(
        "DB_URL",
        "postgresql+psycopg2://inv:change_me@localhost/invdb"
    )
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SECRET_KEY = os.getenv("SECRET_KEY", "supersecret")
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))
    WORK_INSTRUCTION_UPLOAD_FOLDER = os.path.join(
        BASE_DIR, "invapp", "static", "work_instructions"
    )
    WORK_INSTRUCTION_ALLOWED_EXTENSIONS = {"pdf"}

    ZEBRA_PRINTER_HOST = os.getenv("ZEBRA_PRINTER_HOST", "localhost")
    ZEBRA_PRINTER_PORT = int(os.getenv("ZEBRA_PRINTER_PORT", 9100))
