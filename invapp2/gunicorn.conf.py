"""Gunicorn configuration for the Hyperion Operations Inventory app."""
import os

# Network binding configuration. Defaults are suitable for containerized deployments.
bind = os.getenv("GUNICORN_BIND", "0.0.0.0:5000")

# Worker process count with a conservative default for smaller devices like Raspberry Pi.
workers = int(os.getenv("GUNICORN_WORKERS", "2"))

# Log to stdout/stderr by default so container orchestrators can capture logs.
accesslog = os.getenv("GUNICORN_ACCESS_LOGFILE", "-")
errorlog = os.getenv("GUNICORN_ERROR_LOGFILE", "-")

# Optionally allow applications to request graceful handling of forwarded headers.
forwarded_allow_ips = os.getenv("GUNICORN_FORWARDED_ALLOW_IPS", "*")
