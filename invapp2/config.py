import os

class Config:
    SQLALCHEMY_DATABASE_URI = os.getenv(
        "DB_URL",
        "postgresql+psycopg2://inv:change_me@localhost/invdb"
    )
    SQLALCHEMY_TRACK_MODIFICATIONS = False
