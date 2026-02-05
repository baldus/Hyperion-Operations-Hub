import os

from flask import current_app


FLOORPLAN_FILENAME = "inventory_floorplan.pdf"
SITE_UPLOADS_DIRNAME = "site_uploads"


def floorplan_dir() -> str:
    return os.path.join(current_app.instance_path, SITE_UPLOADS_DIRNAME)


def floorplan_path() -> str:
    return os.path.join(floorplan_dir(), FLOORPLAN_FILENAME)


def floorplan_exists() -> bool:
    return os.path.exists(floorplan_path())

