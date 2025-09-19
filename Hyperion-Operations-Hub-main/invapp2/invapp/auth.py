from functools import wraps

from flask import abort, session
from flask_login import current_user, login_required, login_user

from invapp.models import User


def role_required(role_name):
    """Decorator that ensures the current user has the given role."""

    def decorator(f):
        @wraps(f)
        @login_required
        def wrapped(*args, **kwargs):
            if not current_user.has_role(role_name):
                abort(403)
            return f(*args, **kwargs)

        return wrapped

    return decorator


def refresh_logged_in_user():
    """Return a managed ``User`` for the current session if available."""

    user_id = session.get("_user_id")
    if not user_id:
        return None

    user = User.query.get(int(user_id))
    if user is None:
        return None

    login_user(user)
    return user
