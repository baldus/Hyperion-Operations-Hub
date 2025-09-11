from functools import wraps
from flask_login import login_required, current_user
from flask import abort


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
