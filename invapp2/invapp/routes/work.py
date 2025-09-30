import os
import uuid
from flask import (
    Blueprint,
    current_app,
    redirect,
    render_template,
    request,
    url_for,
)
from invapp.auth import blueprint_page_guard
from werkzeug.utils import secure_filename

from invapp.login import current_user
from invapp.models import db, WorkInstruction
from invapp.security import require_roles

bp = Blueprint("work", __name__, url_prefix="/work")

bp.before_request(blueprint_page_guard("work"))


def _allowed_file(filename: str) -> bool:
    return (
        "." in filename
        and filename.rsplit(".", 1)[1].lower()
        in current_app.config["WORK_INSTRUCTION_ALLOWED_EXTENSIONS"]
    )


@bp.route("/")
def work_home():
    return redirect(url_for("work.list_instructions"))


@bp.route("/instructions")
def list_instructions():
    instructions = WorkInstruction.query.order_by(
        WorkInstruction.uploaded_at.desc()
    ).all()
    return render_template(
        "work/home.html",
        instructions=instructions,
        is_admin=current_user.is_authenticated and current_user.has_role("admin"),
    )


@bp.route("/instructions/upload", methods=["POST"])
@require_roles("admin")
def upload_instruction():
    file = request.files.get("file")
    if not file or file.filename == "" or not _allowed_file(file.filename):
        return redirect(url_for("work.list_instructions"))

    filename = secure_filename(file.filename)
    unique_name = f"{uuid.uuid4().hex}_{filename}"
    upload_folder = current_app.config["WORK_INSTRUCTION_UPLOAD_FOLDER"]
    os.makedirs(upload_folder, exist_ok=True)
    file_path = os.path.join(upload_folder, unique_name)
    file.save(file_path)

    wi = WorkInstruction(filename=unique_name, original_name=filename)
    db.session.add(wi)
    db.session.commit()

    return redirect(url_for("work.list_instructions"))


@bp.route("/instructions/<int:instruction_id>/delete", methods=["POST"])
@require_roles("admin")
def delete_instruction(instruction_id):
    wi = WorkInstruction.query.get_or_404(instruction_id)
    file_path = os.path.join(
        current_app.config["WORK_INSTRUCTION_UPLOAD_FOLDER"], wi.filename
    )
    if os.path.exists(file_path):
        os.remove(file_path)
    db.session.delete(wi)
    db.session.commit()
    return redirect(url_for("work.list_instructions"))

