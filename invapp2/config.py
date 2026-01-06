import os

class Config:
    SQLALCHEMY_DATABASE_URI = os.getenv(
        "DB_URL",
        "postgresql+psycopg2://inv:change_me@localhost/invdb"
    )
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SECRET_KEY = os.getenv("SECRET_KEY", "supersecret")
    ADMIN_USER = os.getenv("ADMIN_USER", "superuser")
    ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "joshbaldus")
    ADMIN_SESSION_TIMEOUT = int(os.getenv("ADMIN_SESSION_TIMEOUT", 300))
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))
    WORK_INSTRUCTION_UPLOAD_FOLDER = os.path.join(
        BASE_DIR, "invapp", "static", "work_instructions"
    )
    WORK_INSTRUCTION_ALLOWED_EXTENSIONS = {"pdf"}

    MDI_DEFAULT_RECIPIENTS = os.getenv("MDI_DEFAULT_RECIPIENTS", "")
    # Leave sender blank by default so mail clients can select the active account
    # automatically when the draft is opened. Configure via env var if desired.
    MDI_DEFAULT_SENDER = os.getenv("MDI_DEFAULT_SENDER", "")

    FRAMING_PANEL_OFFSET = os.getenv("FRAMING_PANEL_OFFSET", 0)

    ITEM_ATTACHMENT_UPLOAD_FOLDER = os.path.join(
        BASE_DIR, "invapp", "static", "item_attachments"
    )
    ITEM_ATTACHMENT_ALLOWED_EXTENSIONS = {"pdf", "png", "jpg", "jpeg"}

    QUALITY_ATTACHMENT_UPLOAD_FOLDER = os.path.join(
        BASE_DIR, "invapp", "static", "rma_attachments"
    )
    QUALITY_ATTACHMENT_ALLOWED_EXTENSIONS = {
        "pdf",
        "png",
        "jpg",
        "jpeg",
        "gif",
        "doc",
        "docx",
        "xls",
        "xlsx",
        "csv",
        "txt",
    }
    PURCHASING_ATTACHMENT_UPLOAD_FOLDER = os.getenv(
        "PURCHASING_ATTACHMENT_UPLOAD_FOLDER",
        os.path.join(BASE_DIR, "invapp", "static", "purchase_request_attachments"),
    )
    PURCHASING_ATTACHMENT_ALLOWED_EXTENSIONS = {
        "pdf",
        "png",
        "jpg",
        "jpeg",
        "xlsx",
        "csv",
        "txt",
        "doc",
        "docx",
    }
    PURCHASING_ATTACHMENT_MAX_SIZE_MB = int(
        os.getenv("PURCHASING_ATTACHMENT_MAX_SIZE_MB", 25)
    )

    ZEBRA_PRINTER_HOST = os.getenv("ZEBRA_PRINTER_HOST", "localhost")
    ZEBRA_PRINTER_PORT = int(os.getenv("ZEBRA_PRINTER_PORT", 9100))
