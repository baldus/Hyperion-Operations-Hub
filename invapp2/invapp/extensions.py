"""Application extensions.

Currently only SQLAlchemy was in use. This module now also exposes a
``LoginManager`` instance so the application factory can configure session
handling and user authentication in a single place.
"""

from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager

db = SQLAlchemy()
# Login manager used by Flask-Login for session handling
login_manager = LoginManager()
