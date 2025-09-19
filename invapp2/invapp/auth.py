from functools import wraps

from flask import abort, session
from flask_login import current_user

from invapp.extensions import login_manager


def role_required(role_name):
    """Decorator that ensures the current user has the given role."""

    def decorator(f):
        @wraps(f)
        def wrapped(*args, **kwargs):
            if session.get("is_admin"):
                return f(*args, **kwargs)

            if current_user.is_authenticated:
                if current_user.has_role(role_name):
                    return f(*args, **kwargs)
                abort(403)

            from invapp.models import User

            if User.query.first() is None:
                return f(*args, **kwargs)

            return login_manager.unauthorized()

        return wrapped

    return decorator
