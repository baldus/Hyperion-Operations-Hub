"""Application-wide extension singletons."""

from flask_login import LoginManager
from flask_sqlalchemy import SQLAlchemy


db = SQLAlchemy()
login_manager = LoginManager()

# Configure the default login view so ``login_required`` knows where to send
# anonymous users.  The value is imported lazily to avoid circular imports when
# the extensions module is imported by ``invapp.__init__`` during application
# factory setup.
login_manager.login_view = "auth.login"
login_manager.login_message_category = "warning"
