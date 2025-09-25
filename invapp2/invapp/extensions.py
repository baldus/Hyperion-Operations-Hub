from flask_sqlalchemy import SQLAlchemy

from .login import LoginManager

db = SQLAlchemy()
login_manager = LoginManager()
