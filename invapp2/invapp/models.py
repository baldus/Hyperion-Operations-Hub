import logging
import secrets
from decimal import Decimal
from datetime import date, datetime
from typing import ClassVar

from flask import current_app
from flask_sqlalchemy import BaseQuery
from sqlalchemy import func, inspect, select, text
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.associationproxy import association_proxy
from sqlalchemy.orm import synonym
from sqlalchemy.orm.exc import DetachedInstanceError
from werkzeug.security import check_password_hash, generate_password_hash

from invapp.extensions import db
from invapp.login import UserMixin
from invapp.db_maintenance import repair_primary_key_sequences


class PrimaryKeySequenceMixin:
    """Helpers to repair auto-increment sequences after manual imports."""

    pk_constraint_name: ClassVar[str]
    pk_column_name: ClassVar[str] = "id"

    @classmethod
    def _is_duplicate_pk_error(cls, error: IntegrityError) -> bool:
        """Return True when the IntegrityError represents a PK collision."""

        if not isinstance(error, IntegrityError):
            return False

        original = getattr(error, "orig", None)
        pgcode = getattr(original, "pgcode", None)
        if pgcode == "23505":  # unique_violation
            constraint = getattr(original, "diag", None)
            constraint_name = getattr(constraint, "constraint_name", None)
            if constraint_name:
                return constraint_name == getattr(cls, "pk_constraint_name", None)

        message = str(error).lower()
        constraint_name = getattr(cls, "pk_constraint_name", "")
        if constraint_name and constraint_name.lower() in message:
            return True

        table_name = getattr(cls, "__tablename__", "").lower()
        column_name = getattr(cls, "pk_column_name", "id").lower()
        return f"{table_name}.{column_name}" in message or (
            f'"{table_name}".{column_name}' in message
        )

    @classmethod
    def _repair_primary_key_sequence(cls) -> None:
        """Ensure the backing sequence advances past the current max id."""

        bind = db.session.bind or getattr(db, "engine", None)
        if not bind:
            return

        try:
            logger = current_app.logger  # type: ignore[attr-defined]
        except Exception:
            logger = logging.getLogger(__name__)

        try:
            repair_primary_key_sequences(
                bind,
                db.Model,
                logger=logger,
                models=[cls],
            )
        except SQLAlchemyError as exc:
            logger.warning(
                "Failed to repair primary key sequence for %s: %s",
                getattr(cls, "__tablename__", cls.__name__),
                exc,
                exc_info=logger.isEnabledFor(logging.DEBUG),
            )

    @classmethod
    def commit_with_sequence_retry(cls, instance) -> None:
        """Persist a new instance, repairing the PK sequence if needed."""

        db.session.add(instance)
        try:
            db.session.commit()
        except IntegrityError as error:
            if not cls._is_duplicate_pk_error(error):
                db.session.rollback()
                raise

            db.session.rollback()
            cls._repair_primary_key_sequence()
            setattr(instance, getattr(cls, "pk_column_name", "id"), None)
            db.session.add(instance)
            db.session.commit()


class SoftDeleteQuery(BaseQuery):
    _with_removed = False

    def with_removed(self):
        query = self._clone()
        query._with_removed = True
        return query

    def _only_not_removed(self):
        if self._with_removed:
            return self
        try:
            mapper = self._only_full_mapper_zero("soft delete")
        except Exception:
            return self
        model = mapper.class_
        if hasattr(model, "removed_at"):
            return self.enable_assertions(False).filter(model.removed_at.is_(None))
        return self

    def __iter__(self):
        return super(SoftDeleteQuery, self._only_not_removed()).__iter__()

    def get(self, ident):
        if self._with_removed:
            return super().get(ident)
        obj = super().get(ident)
        if obj is not None and getattr(obj, "removed_at", None) is not None:
            return None
        return obj

    def first(self):
        return super(SoftDeleteQuery, self._only_not_removed()).first()

    def one(self):
        return super(SoftDeleteQuery, self._only_not_removed()).one()

    def one_or_none(self):
        return super(SoftDeleteQuery, self._only_not_removed()).one_or_none()

    def count(self):
        return super(SoftDeleteQuery, self._only_not_removed()).count()

    def delete(self, synchronize_session="evaluate"):
        if self._with_removed:
            return super().delete(synchronize_session=synchronize_session)
        try:
            mapper = self._only_full_mapper_zero("soft delete")
        except Exception:
            return super().delete(synchronize_session=synchronize_session)
        model = mapper.class_
        if hasattr(model, "removed_at"):
            return super().update(
                {model.removed_at: datetime.utcnow()},
                synchronize_session=synchronize_session,
            )
        return super().delete(synchronize_session=synchronize_session)


class AccessLog(db.Model):
    __tablename__ = "access_log"

    EVENT_REQUEST = "request"
    EVENT_LOGIN_SUCCESS = "login_success"
    EVENT_LOGIN_FAILURE = "login_failure"
    EVENT_LOGOUT = "logout"

    EVENT_LABELS = {
        EVENT_REQUEST: "Request",
        EVENT_LOGIN_SUCCESS: "Login Success",
        EVENT_LOGIN_FAILURE: "Login Failure",
        EVENT_LOGOUT: "Logout",
    }

    id = db.Column(db.Integer, primary_key=True)
    occurred_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    event_type = db.Column(db.String(64), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id", ondelete="SET NULL"))
    username = db.Column(db.String(255), nullable=True)
    ip_address = db.Column(db.String(64), nullable=True)
    user_agent = db.Column(db.String(512), nullable=True)
    method = db.Column(db.String(16), nullable=True)
    path = db.Column(db.String(512), nullable=True)
    endpoint = db.Column(db.String(255), nullable=True)
    status_code = db.Column(db.Integer, nullable=True)
    details = db.Column(db.JSON, nullable=True)

    user = db.relationship("User", backref=db.backref("access_logs", lazy="dynamic"))

    __table_args__ = (
        db.Index("ix_access_log_occurred_at", "occurred_at"),
        db.Index("ix_access_log_event_type", "event_type"),
    )

    @classmethod
    def label_for_event(cls, event_type: str) -> str:
        return cls.EVENT_LABELS.get(event_type, event_type.title())



class ErrorReport(db.Model):
    __tablename__ = "error_report"

    id = db.Column(db.Integer, primary_key=True)
    occurred_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    path = db.Column(db.String(512), nullable=True)
    endpoint = db.Column(db.String(255), nullable=True)
    message = db.Column(db.Text, nullable=False)
    stacktrace = db.Column(db.Text, nullable=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id", ondelete="SET NULL"))
    username = db.Column(db.String(255), nullable=True)
    user_agent = db.Column(db.String(512), nullable=True)
    ip_address = db.Column(db.String(64), nullable=True)

    user = db.relationship("User", backref=db.backref("error_reports", lazy="dynamic"))

    __table_args__ = (db.Index("ix_error_report_occurred_at", "occurred_at"),)

    def summary(self) -> str:
        """Return the first non-empty line of the message for display."""

        for line in (self.message or "").splitlines():
            cleaned = line.strip()
            if cleaned:
                return cleaned
        return self.message or "Internal error"

class UsefulLink(db.Model):
    __tablename__ = "useful_link"

    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(255), nullable=False)
    url = db.Column(db.String(2048), nullable=False)
    description = db.Column(db.String(512), nullable=True)
    display_order = db.Column(db.Integer, nullable=False, default=0)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(
        db.DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    __table_args__ = (db.Index("ix_useful_link_display_order", "display_order"),)

    @classmethod
    def ordered(cls):
        return cls.query.order_by(cls.display_order.asc(), cls.title.asc()).all()


class AppSetting(db.Model):
    __tablename__ = "app_setting"

    id = db.Column(db.Integer, primary_key=True)
    key = db.Column(db.String(128), nullable=False, unique=True)
    value = db.Column(db.String(255), nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(
        db.DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    @classmethod
    def get_or_create(cls, key: str, default_value: str | None = None):
        setting = cls.query.filter_by(key=key).first()
        if setting is None:
            setting = cls(key=key, value=default_value)
            db.session.add(setting)
            db.session.commit()
        return setting


class BackupRestoreEvent(db.Model):
    __tablename__ = "backup_restore_event"

    id = db.Column(db.Integer, primary_key=True)
    occurred_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id", ondelete="SET NULL"))
    username = db.Column(db.String(255), nullable=True)
    backup_filename = db.Column(db.String(255), nullable=False)
    action = db.Column(db.String(64), nullable=False)
    status = db.Column(db.String(32), nullable=False)
    message = db.Column(db.Text, nullable=True)

    user = db.relationship("User", backref=db.backref("backup_restore_events", lazy="dynamic"))

    __table_args__ = (
        db.Index("ix_backup_restore_event_occurred_at", "occurred_at"),
    )


class BackupRun(db.Model):
    __tablename__ = "backup_run"

    id = db.Column(db.Integer, primary_key=True)
    started_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    finished_at = db.Column(db.DateTime, nullable=True)
    status = db.Column(db.String(32), nullable=False)
    filename = db.Column(db.String(255), nullable=True)
    filepath = db.Column(db.String(512), nullable=True)
    bytes = db.Column(db.BigInteger, nullable=True)
    message = db.Column(db.Text, nullable=True)

    __table_args__ = (db.Index("ix_backup_run_started_at", "started_at"),)


class OpsEventLog(db.Model):
    __tablename__ = "ops_event_log"

    id = db.Column(db.Integer, primary_key=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    level = db.Column(db.String(32), nullable=False)
    source = db.Column(db.String(128), nullable=True)
    message = db.Column(db.Text, nullable=False)
    context_json = db.Column(db.JSON, nullable=True)

    __table_args__ = (db.Index("ix_ops_event_log_created_at", "created_at"),)


class AdminAuditLog(db.Model):
    __tablename__ = "admin_audit_log"

    id = db.Column(db.Integer, primary_key=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id", ondelete="SET NULL"))
    action = db.Column(db.String(64), nullable=False)
    note = db.Column(db.Text, nullable=True)
    request_ip = db.Column(db.String(64), nullable=True)

    user = db.relationship("User", backref=db.backref("admin_audit_logs", lazy="dynamic"))

    __table_args__ = (db.Index("ix_admin_audit_log_created_at", "created_at"),)


class ProductionChartSettings(db.Model):
    __tablename__ = "production_chart_settings"

    id = db.Column(db.Integer, primary_key=True)
    primary_min = db.Column(db.Numeric(10, 2), nullable=True)
    primary_max = db.Column(db.Numeric(10, 2), nullable=True)
    primary_step = db.Column(db.Numeric(10, 2), nullable=True)
    secondary_min = db.Column(db.Numeric(10, 2), nullable=True)
    secondary_max = db.Column(db.Numeric(10, 2), nullable=True)
    secondary_step = db.Column(db.Numeric(10, 2), nullable=True)
    goal_value = db.Column(db.Numeric(10, 2), nullable=True)
    show_goal = db.Column(db.Boolean, nullable=False, default=False)

    @classmethod
    def get_or_create(cls):
        settings = cls.query.first()
        if settings is None:
            settings = cls()
            db.session.add(settings)
            db.session.commit()
        return settings


class FramingSettings(db.Model):
    __tablename__ = "framing_settings"

    id = db.Column(db.Integer, primary_key=True)
    panel_length_offset = db.Column(db.Numeric(10, 2), nullable=False, default=0)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(
        db.DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    @classmethod
    def get_or_create(cls):
        settings = cls.query.first()
        if settings is None:
            settings = cls()
            db.session.add(settings)
            db.session.commit()
        return settings


class Printer(db.Model):
    __tablename__ = "printer"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False, unique=True)
    printer_type = db.Column(db.String(80), nullable=True)
    location = db.Column(db.String(120), nullable=True)
    host = db.Column(db.String(255), nullable=False)
    port = db.Column(db.Integer, nullable=True)
    notes = db.Column(db.String(255), nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(
        db.DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    def connection_label(self) -> str:
        port_display = f":{self.port}" if self.port else ""
        return f"{self.host}{port_display}"


class LabelTemplate(db.Model):
    __tablename__ = "label_template"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False, unique=True)
    description = db.Column(db.String(255), nullable=True)
    trigger = db.Column(db.String(120), nullable=True)
    layout = db.Column(db.JSON, nullable=False, default=dict)
    fields = db.Column(db.JSON, nullable=False, default=dict)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(
        db.DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow
    )


class LabelProcessAssignment(db.Model):
    __tablename__ = "label_process_assignment"

    id = db.Column(db.Integer, primary_key=True)
    process = db.Column(db.String(120), nullable=False, unique=True)
    template_id = db.Column(db.Integer, db.ForeignKey("label_template.id"), nullable=False)
    template = db.relationship(
        "LabelTemplate",
        backref=db.backref("assignments", cascade="all, delete-orphan", lazy="joined"),
    )
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(
        db.DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow
    )


class Item(db.Model):
    __tablename__ = "item"
    __table_args__ = (
        db.Index("ix_item_secondary_location_id", "secondary_location_id"),
        db.Index("ix_item_point_of_use_location_id", "point_of_use_location_id"),
    )
    id = db.Column(db.Integer, primary_key=True)  # system key
    sku = db.Column(db.String, unique=True, nullable=False)  # part number
    name = db.Column(db.String, nullable=False)
    type = db.Column(db.String)
    unit = db.Column(db.String, default="ea")
    description = db.Column(db.String)
    min_stock = db.Column(db.Integer, default=0)
    notes = db.Column(db.Text)
    list_price = db.Column(db.Numeric(12, 2))
    last_unit_cost = db.Column(db.Numeric(12, 2))
    item_class = db.Column(db.String)
    default_location_id = db.Column(
        db.Integer, db.ForeignKey("location.id"), nullable=True
    )
    secondary_location_id = db.Column(
        db.Integer, db.ForeignKey("location.id"), nullable=True
    )
    point_of_use_location_id = db.Column(
        db.Integer, db.ForeignKey("location.id"), nullable=True
    )

    default_location = db.relationship(
        "Location",
        foreign_keys=[default_location_id],
        lazy="joined",
    )
    secondary_location = db.relationship(
        "Location",
        foreign_keys=[secondary_location_id],
        lazy="joined",
    )
    point_of_use_location = db.relationship(
        "Location",
        foreign_keys=[point_of_use_location_id],
        lazy="joined",
    )
    # NOTE: ``default_location_id`` is backfilled for legacy databases in
    # ``_ensure_inventory_schema`` to avoid model/schema drift.

    attachments = db.relationship(
        "ItemAttachment",
        back_populates="item",
        cascade="all, delete-orphan",
        order_by="ItemAttachment.uploaded_at.desc()",
    )


class ItemAttachment(db.Model):
    __tablename__ = "item_attachment"

    id = db.Column(db.Integer, primary_key=True)
    item_id = db.Column(db.Integer, db.ForeignKey("item.id"), nullable=False)
    filename = db.Column(db.String, nullable=False)
    original_name = db.Column(db.String, nullable=False)
    uploaded_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    item = db.relationship("Item", back_populates="attachments")


class Location(db.Model):
    __tablename__ = "location"
    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String, unique=True, nullable=False)
    description = db.Column(db.String)


class Batch(db.Model):
    __tablename__ = "batch"
    query_class = SoftDeleteQuery
    id = db.Column(db.Integer, primary_key=True)
    item_id = db.Column(db.Integer, db.ForeignKey("item.id"), nullable=False)
    lot_number = db.Column(db.String, nullable=True)  # supplier batch/lot reference
    quantity = db.Column(db.Numeric(12, 3), nullable=True)
    removed_at = db.Column(db.DateTime, nullable=True)
    received_date = db.Column(db.DateTime, default=datetime.utcnow)
    expiration_date = db.Column(db.Date, nullable=True)
    supplier_name = db.Column(db.String, nullable=True)
    supplier_code = db.Column(db.String, nullable=True)
    purchase_order = db.Column(db.String, nullable=True)
    notes = db.Column(db.Text, nullable=True)

    item = db.relationship("Item", backref="batches")

    __table_args__ = (db.Index("ix_batch_removed_at", "removed_at"),)

    @classmethod
    def active(cls):
        return cls.query.filter(cls.removed_at.is_(None))

    @classmethod
    def with_removed(cls):
        return cls.query.with_removed()

    def soft_delete(self, removed_at: datetime | None = None) -> None:
        self.removed_at = removed_at or datetime.utcnow()


class Movement(PrimaryKeySequenceMixin, db.Model):
    __tablename__ = "movement"
    pk_constraint_name: ClassVar[str] = "movement_pkey"
    id = db.Column(db.Integer, primary_key=True)
    item_id = db.Column(db.Integer, db.ForeignKey("item.id"), nullable=False)
    batch_id = db.Column(db.Integer, db.ForeignKey("batch.id"), nullable=True)
    location_id = db.Column(db.Integer, db.ForeignKey("location.id"), nullable=False)
    quantity = db.Column(db.Numeric(12, 3), nullable=False)
    movement_type = db.Column(db.String, nullable=False)  # RECEIPT, ISSUE, MOVE, ADJUST
    person = db.Column(db.String, nullable=True)
    po_number = db.Column(db.String, nullable=True)
    reference = db.Column(db.String, nullable=True)
    date = db.Column(db.DateTime, default=datetime.utcnow)

    item = db.relationship("Item", backref="movements")
    batch = db.relationship("Batch", backref="movements")
    location = db.relationship("Location", backref="movements")


class PurchaseRequest(PrimaryKeySequenceMixin, db.Model):
    __tablename__ = "purchase_request"

    pk_constraint_name: ClassVar[str] = "purchase_request_pkey"

    STATUS_NEW = "new"
    STATUS_REVIEW = "review"
    STATUS_WAITING = "waiting"
    STATUS_ORDERED = "ordered"
    STATUS_RECEIVED = "received"
    STATUS_CANCELLED = "cancelled"

    STATUS_CHOICES: tuple[tuple[str, str], ...] = (
        (STATUS_NEW, "New"),
        (STATUS_REVIEW, "Reviewing"),
        (STATUS_WAITING, "Waiting on Supplier"),
        (STATUS_ORDERED, "Ordered"),
        (STATUS_RECEIVED, "Received"),
        (STATUS_CANCELLED, "Cancelled"),
    )

    id = db.Column(db.Integer, primary_key=True)
    item_id = db.Column(db.Integer, db.ForeignKey("item.id"), nullable=True)
    item_number = db.Column(db.String(255), nullable=True)
    title = db.Column(db.String(255), nullable=False)
    description = db.Column(db.Text, nullable=True)
    quantity = db.Column(db.Numeric(10, 2), nullable=True)
    unit = db.Column(db.String(32), nullable=True)
    requested_by = db.Column(db.String(128), nullable=False)
    needed_by = db.Column(db.Date, nullable=True)
    status = db.Column(db.String(32), nullable=False, default=STATUS_NEW)
    supplier_name = db.Column(db.String(255), nullable=True)
    supplier_contact = db.Column(db.String(255), nullable=True)
    eta_date = db.Column(db.Date, nullable=True)
    purchase_order_number = db.Column(db.String(64), nullable=True)
    notes = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(
        db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )

    attachments = db.relationship(
        "PurchaseRequestAttachment",
        back_populates="request",
        cascade="all, delete-orphan",
        order_by="PurchaseRequestAttachment.uploaded_at.desc()",
    )
    item = db.relationship("Item")

    @classmethod
    def status_values(cls) -> tuple[str, ...]:
        return tuple(choice for choice, _ in cls.STATUS_CHOICES)

    @classmethod
    def status_label(cls, value: str) -> str:
        labels = dict(cls.STATUS_CHOICES)
        return labels.get(value, value.replace("_", " ").title())

    def mark_status(self, new_status: str) -> None:
        if new_status not in self.status_values():
            raise ValueError(f"Invalid purchase request status: {new_status}")
        self.status = new_status

    @property
    def is_closed(self) -> bool:
        return self.status in {self.STATUS_RECEIVED, self.STATUS_CANCELLED}


class PurchaseRequestAttachment(db.Model):
    __tablename__ = "purchase_request_attachment"

    id = db.Column(db.Integer, primary_key=True)
    request_id = db.Column(
        db.Integer, db.ForeignKey("purchase_request.id"), nullable=False
    )
    filename = db.Column(db.String, nullable=False)
    original_name = db.Column(db.String, nullable=False)
    file_size = db.Column(db.Integer, nullable=False, default=0)
    uploaded_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    uploaded_by = db.Column(db.String(128), nullable=True)

    request = db.relationship("PurchaseRequest", back_populates="attachments")


class PurchaseRequestDeleteAudit(db.Model):
    __tablename__ = "purchase_request_delete_audit"

    id = db.Column(db.Integer, primary_key=True)
    purchase_request_id = db.Column(db.Integer, nullable=False)
    title = db.Column(db.String(255), nullable=False)
    item_number = db.Column(db.String(255), nullable=True)
    requested_by = db.Column(db.String(128), nullable=True)
    attachment_count = db.Column(db.Integer, nullable=False, default=0)
    deleted_by_user_id = db.Column(db.Integer, nullable=True)
    deleted_by_username = db.Column(db.String(255), nullable=True)
    deleted_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    delete_reason = db.Column(db.Text, nullable=True)


class RMARequest(db.Model):
    __tablename__ = "rma_request"

    STATUS_OPEN = "open"
    STATUS_IN_REVIEW = "in_review"
    STATUS_NEED_INFO = "need_more_info"
    STATUS_AWAITING_RETURN = "awaiting_return"
    STATUS_PENDING_TECH_REVIEW = "pending_tech_review"
    STATUS_RESOLVED = "resolved"
    STATUS_CLOSED = "closed"

    STATUS_CHOICES: tuple[tuple[str, str], ...] = (
        (STATUS_OPEN, "Open"),
        (STATUS_IN_REVIEW, "In Review"),
        (STATUS_NEED_INFO, "Need More Info"),
        (STATUS_AWAITING_RETURN, "Awaiting Return"),
        (STATUS_PENDING_TECH_REVIEW, "Pending Tech Review"),
        (STATUS_RESOLVED, "Resolved"),
        (STATUS_CLOSED, "Closed"),
    )

    PRIORITY_LOW = "low"
    PRIORITY_NORMAL = "normal"
    PRIORITY_HIGH = "high"
    PRIORITY_CRITICAL = "critical"

    PRIORITY_CHOICES: tuple[tuple[str, str], ...] = (
        (PRIORITY_LOW, "Low"),
        (PRIORITY_NORMAL, "Normal"),
        (PRIORITY_HIGH, "High"),
        (PRIORITY_CRITICAL, "Critical"),
    )

    CLOSED_STATUSES = {STATUS_RESOLVED, STATUS_CLOSED}

    id = db.Column(db.Integer, primary_key=True)
    reference = db.Column(db.String(32), unique=True, nullable=False)
    status = db.Column(db.String(32), nullable=False, default=STATUS_OPEN)
    priority = db.Column(db.String(16), nullable=False, default=PRIORITY_NORMAL)
    opened_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(
        db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )
    closed_at = db.Column(db.DateTime, nullable=True)
    opened_by = db.Column(db.String(128), nullable=False)
    customer_name = db.Column(db.String(255), nullable=False)
    customer_contact = db.Column(db.String(255), nullable=True)
    customer_reference = db.Column(db.String(128), nullable=True)
    product_sku = db.Column(db.String(64), nullable=True)
    product_description = db.Column(db.String(255), nullable=True)
    product_serial = db.Column(db.String(64), nullable=True)
    issue_category = db.Column(db.String(64), nullable=True)
    issue_description = db.Column(db.Text, nullable=False)
    requested_action = db.Column(db.String(255), nullable=True)
    target_resolution_date = db.Column(db.Date, nullable=True)
    resolution = db.Column(db.Text, nullable=True)
    return_tracking_number = db.Column(db.String(128), nullable=True)
    replacement_order_number = db.Column(db.String(128), nullable=True)
    follow_up_tasks = db.Column(db.Text, nullable=True)
    internal_notes = db.Column(db.Text, nullable=True)
    last_customer_contact = db.Column(db.Date, nullable=True)

    attachments = db.relationship(
        "RMAAttachment",
        back_populates="request",
        cascade="all, delete-orphan",
        order_by="RMAAttachment.uploaded_at.desc()",
    )
    status_events = db.relationship(
        "RMAStatusEvent",
        back_populates="request",
        cascade="all, delete-orphan",
        order_by="RMAStatusEvent.changed_at.desc()",
    )

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        if not self.status:
            self.status = self.STATUS_OPEN
        if not self.priority:
            self.priority = self.PRIORITY_NORMAL
        if not self.reference:
            self.reference = self.generate_reference()

    @classmethod
    def generate_reference(cls) -> str:
        prefix = datetime.utcnow().strftime("RMA%Y%m%d")
        for _ in range(10):
            candidate = f"{prefix}-{secrets.token_hex(2).upper()}"
            exists = db.session.query(cls.id).filter_by(reference=candidate).first()
            if not exists:
                return candidate
        return f"{prefix}-{secrets.token_hex(4).upper()}"

    @classmethod
    def status_values(cls) -> tuple[str, ...]:
        return tuple(choice for choice, _ in cls.STATUS_CHOICES)

    @classmethod
    def status_label(cls, value: str) -> str:
        labels = dict(cls.STATUS_CHOICES)
        return labels.get(value, value.replace("_", " ").title())

    @classmethod
    def priority_label(cls, value: str) -> str:
        labels = dict(cls.PRIORITY_CHOICES)
        return labels.get(value, value.replace("_", " ").title())

    def mark_status(self, new_status: str) -> None:
        if new_status not in self.status_values():
            raise ValueError(f"Invalid RMA status: {new_status}")
        previous_status = self.status
        self.status = new_status
        if new_status in self.CLOSED_STATUSES:
            self.closed_at = datetime.utcnow()
        elif previous_status in self.CLOSED_STATUSES and new_status not in self.CLOSED_STATUSES:
            self.closed_at = None

    @property
    def is_closed(self) -> bool:
        return self.status in self.CLOSED_STATUSES

    @property
    def is_open(self) -> bool:
        return not self.is_closed

    @property
    def age_in_days(self) -> int:
        end_point = self.closed_at or datetime.utcnow()
        return (end_point.date() - self.opened_at.date()).days


class RMAAttachment(db.Model):
    __tablename__ = "rma_attachment"

    id = db.Column(db.Integer, primary_key=True)
    request_id = db.Column(db.Integer, db.ForeignKey("rma_request.id"), nullable=False)
    filename = db.Column(db.String, nullable=False)
    original_name = db.Column(db.String, nullable=False)
    uploaded_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    request = db.relationship("RMARequest", back_populates="attachments")


class RMAStatusEvent(db.Model):
    __tablename__ = "rma_status_event"
    # Ensure SQLite allocates monotonically increasing rowids so sequence repair
    # utilities can reset the backing counter without reusing identifiers.
    __table_args__ = {"sqlite_autoincrement": True}

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    request_id = db.Column(db.Integer, db.ForeignKey("rma_request.id"), nullable=False)
    from_status = db.Column(db.String(32), nullable=True)
    to_status = db.Column(db.String(32), nullable=False)
    note = db.Column(db.Text, nullable=True)
    changed_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    changed_by = db.Column(db.String(128), nullable=False)

    request = db.relationship("RMARequest", back_populates="status_events")

class WorkInstruction(db.Model):
    __tablename__ = "work_instruction"
    id = db.Column(db.Integer, primary_key=True)
    filename = db.Column(db.String, nullable=False)
    original_name = db.Column(db.String, nullable=False)
    uploaded_at = db.Column(db.DateTime, default=datetime.utcnow)



class ProductionCustomer(db.Model):
    __tablename__ = "production_customer"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(80), unique=True, nullable=False)
    color = db.Column(db.String(7), nullable=False, default="#3b82f6")
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    is_other_bucket = db.Column(db.Boolean, nullable=False, default=False)
    lump_into_other = db.Column(db.Boolean, nullable=False, default=False)

    totals = db.relationship(
        "ProductionDailyCustomerTotal",
        back_populates="customer",
        cascade="all, delete-orphan",
    )



class ProductionDailyRecord(db.Model):
    __tablename__ = "production_daily_record"

    id = db.Column(db.Integer, primary_key=True)
    entry_date = db.Column(db.Date, unique=True, index=True, nullable=False)
    # Allow room for locales with longer weekday names (e.g., "Donnerstag")
    day_of_week = db.Column(db.String(32), nullable=False)


    gates_employees = db.Column(db.Integer, nullable=False, default=0)
    gates_hours_ot = db.Column(db.Numeric(7, 2), nullable=False, default=0)
    controllers_4_stop = db.Column(db.Integer, nullable=False, default=0)
    controllers_6_stop = db.Column(db.Integer, nullable=False, default=0)
    door_locks_lh = db.Column(db.Integer, nullable=False, default=0)
    door_locks_rh = db.Column(db.Integer, nullable=False, default=0)
    operators_produced = db.Column(db.Integer, nullable=False, default=0)
    cops_produced = db.Column(db.Integer, nullable=False, default=0)
    additional_employees = db.Column(db.Integer, nullable=False, default=0)
    additional_hours_ot = db.Column(db.Numeric(7, 2), nullable=False, default=0)
    gates_notes = db.Column(db.Text, nullable=True)
    gates_summary = db.Column(db.Text, nullable=True)
    additional_notes = db.Column(db.Text, nullable=True)
    additional_summary = db.Column(db.Text, nullable=True)
    daily_notes = db.Column(db.Text, nullable=True)

    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(
        db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )


    customer_totals = db.relationship(
        "ProductionDailyCustomerTotal",
        back_populates="record",
        cascade="all, delete-orphan",
        lazy="joined",
    )
    gate_completions = db.relationship(
        "ProductionDailyGateCompletion",
        back_populates="record",
        cascade="all, delete-orphan",
        lazy="joined",
        order_by="ProductionDailyGateCompletion.created_at.asc()",
    )

    LABOR_SHIFT_HOURS = Decimal("8.0")

    @property
    def total_gates_produced(self) -> int:
        return sum(total.gates_produced or 0 for total in self.customer_totals)

    @property
    def total_gates_packaged(self) -> int:
        return sum(total.gates_packaged or 0 for total in self.customer_totals)


    @property
    def total_controllers(self) -> int:
        return (self.controllers_4_stop or 0) + (self.controllers_6_stop or 0)

    @property
    def total_door_locks(self) -> int:
        return (self.door_locks_lh or 0) + (self.door_locks_rh or 0)

    @property
    def gates_total_labor_hours(self) -> Decimal:
        employees = Decimal(self.gates_employees or 0)
        overtime = self.gates_hours_ot or Decimal("0")
        return (employees * self.LABOR_SHIFT_HOURS + overtime).quantize(
            Decimal("0.01")
        )

    @property
    def additional_total_labor_hours(self) -> Decimal:
        employees = Decimal(self.additional_employees or 0)
        overtime = self.additional_hours_ot or Decimal("0")
        return (employees * self.LABOR_SHIFT_HOURS + overtime).quantize(
            Decimal("0.01")
        )


class ProductionDailyCustomerTotal(db.Model):
    __tablename__ = "production_daily_customer_total"

    id = db.Column(db.Integer, primary_key=True)
    record_id = db.Column(
        db.Integer,
        db.ForeignKey("production_daily_record.id"),
        nullable=False,
    )
    customer_id = db.Column(
        db.Integer,
        db.ForeignKey("production_customer.id"),
        nullable=False,
    )
    gates_produced = db.Column(db.Integer, nullable=False, default=0)
    gates_packaged = db.Column(db.Integer, nullable=False, default=0)

    record = db.relationship(
        "ProductionDailyRecord",
        back_populates="customer_totals",
    )
    customer = db.relationship(
        "ProductionCustomer",
        back_populates="totals",
    )


class ProductionDailyGateCompletion(db.Model):
    __tablename__ = "production_daily_gate_completion"

    id = db.Column(db.Integer, primary_key=True)
    record_id = db.Column(
        db.Integer,
        db.ForeignKey("production_daily_record.id"),
        nullable=False,
    )
    order_number = db.Column(db.String(64), nullable=False)
    customer_name = db.Column(db.String(120), nullable=True)
    gates_completed = db.Column(db.Integer, nullable=False, default=0)
    po_number = db.Column(db.String(64), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(
        db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )

    record = db.relationship(
        "ProductionDailyRecord",
        back_populates="gate_completions",
    )



class ProductionOutputFormula(db.Model):
    __tablename__ = "production_output_formula"

    id = db.Column(db.Integer, primary_key=True)
    formula = db.Column(db.Text, nullable=False)
    variables = db.Column(db.JSON, nullable=False, default=list)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(
        db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )


user_roles = db.Table(
    "user_role",
    db.Column("user_id", db.Integer, db.ForeignKey("user.id"), primary_key=True),
    db.Column("role_id", db.Integer, db.ForeignKey("role.id"), primary_key=True),
)


page_access_roles = db.Table(
    "page_access_role",
    db.Column(
        "access_rule_id",
        db.Integer,
        db.ForeignKey("page_access_rule.id"),
        primary_key=True,
    ),
    db.Column("role_id", db.Integer, db.ForeignKey("role.id"), primary_key=True),
)


page_edit_roles = db.Table(
    "page_edit_role",
    db.Column(
        "access_rule_id",
        db.Integer,
        db.ForeignKey("page_access_rule.id"),
        primary_key=True,
    ),
    db.Column("role_id", db.Integer, db.ForeignKey("role.id"), primary_key=True),
)


class Role(db.Model):
    __tablename__ = "role"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(64), unique=True, nullable=False)
    description = db.Column(db.String(255))

    users = db.relationship(
        "User",
        secondary=user_roles,
        back_populates="roles",
    )

    def __repr__(self) -> str:  # pragma: no cover - debug helper
        return f"<Role {self.name}>"


class PageAccessRule(db.Model):
    __tablename__ = "page_access_rule"

    id = db.Column(db.Integer, primary_key=True)
    page_name = db.Column(db.String(128), unique=True, nullable=False)
    label = db.Column(db.String(255), nullable=False)

    view_roles = db.relationship(
        "Role",
        secondary=page_access_roles,
        lazy="joined",
    )

    edit_roles = db.relationship(
        "Role",
        secondary=page_edit_roles,
        lazy="joined",
    )

    @property
    def roles(self):
        """Backward compatible alias for ``view_roles``."""

        return self.view_roles

    @roles.setter
    def roles(self, roles):
        self.view_roles = roles

    def assigned_role_names(self) -> list[str]:
        return sorted({role.name for role in self.view_roles})

    def assigned_view_role_names(self) -> list[str]:
        return sorted({role.name for role in self.view_roles})

    def assigned_edit_role_names(self) -> list[str]:
        return sorted({role.name for role in self.edit_roles})

    def __repr__(self) -> str:  # pragma: no cover - debug helper
        return (
            f"<PageAccessRule {self.page_name} "
            f"view={self.assigned_view_role_names()} "
            f"edit={self.assigned_edit_role_names()}>"
        )


class User(UserMixin, PrimaryKeySequenceMixin, db.Model):
    __tablename__ = "user"

    pk_constraint_name: ClassVar[str] = "user_pkey"

    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(255), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(
        db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )

    roles = db.relationship(
        "Role",
        secondary=user_roles,
        back_populates="users",
        lazy="joined",
    )

    def set_password(self, password: str) -> None:
        self.password_hash = generate_password_hash(password)

    def check_password(self, password: str) -> bool:
        return check_password_hash(self.password_hash, password)

    def has_role(self, role_name: str) -> bool:
        return self.has_any_role((role_name,))

    def has_any_role(self, role_names) -> bool:
        if not role_names:
            return False

        try:
            role_name_set = {role.name for role in self.roles}
        except DetachedInstanceError:
            identity = inspect(self).identity
            if not identity:
                return False
            refreshed = db.session.get(User, identity[0])
            if refreshed is None:
                return False
            return refreshed.has_any_role(role_names)
        except TypeError:
            return False

        return any(name in role_name_set for name in role_names)

    def __repr__(self) -> str:  # pragma: no cover - debug helper
        return f"<User {self.username}>"


class UserHomeLayout(db.Model):
    __tablename__ = "user_home_layout"

    user_id = db.Column(
        db.Integer,
        db.ForeignKey("user.id", ondelete="CASCADE"),
        primary_key=True,
    )
    layout_json = db.Column(db.JSON, nullable=False, default=list)
    updated_at = db.Column(
        db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )

    user = db.relationship("User", backref=db.backref("home_layout", uselist=False))



class OrderStatus:
    SCHEDULED = "SCHEDULED"
    OPEN = "OPEN"
    WAITING_MATERIAL = "WAITING_MATERIAL"
    CLOSED = "CLOSED"
    CANCELLED = "CANCELLED"

    ACTIVE_STATES = {SCHEDULED, OPEN, WAITING_MATERIAL}
    RESERVABLE_STATES = {SCHEDULED, OPEN}
    ALL_STATUSES = [SCHEDULED, OPEN, WAITING_MATERIAL, CLOSED, CANCELLED]
    LABELS = {
        SCHEDULED: "Scheduled",
        OPEN: "Open",
        WAITING_MATERIAL: "Waiting on Material",
        CLOSED: "Closed",
        CANCELLED: "Cancelled",
    }


class Order(db.Model):
    __tablename__ = "order"

    __table_args__ = (
        db.CheckConstraint(
            "(scheduled_start_date IS NULL) OR "
            "(scheduled_completion_date IS NULL) OR "
            "(scheduled_start_date <= scheduled_completion_date)",
            name="ck_order_schedule_window",
        ),
        db.CheckConstraint(
            "(promised_date IS NULL) OR (scheduled_completion_date IS NULL) OR "
            "(promised_date >= scheduled_completion_date)",
            name="ck_order_promised_after_completion",
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
    order_number = db.Column(db.String, unique=True, nullable=False)
    order_type = db.Column(db.String, nullable=False, default="Gates")
    purchase_order_number = db.Column(db.String, nullable=False, default="")
    priority = db.Column(db.Integer, nullable=False, default=0)
    scheduled_ship_date = db.Column(db.Date, nullable=True)
    status = db.Column(db.String, nullable=False, default=OrderStatus.SCHEDULED)
    customer_name = db.Column(db.String, nullable=True)
    created_by = db.Column(db.String, nullable=True)
    general_notes = db.Column(db.Text, nullable=True)
    promised_date = db.Column(db.Date, nullable=True)
    scheduled_start_date = db.Column(db.Date, nullable=True)
    scheduled_completion_date = db.Column(db.Date, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(
        db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )

    order_lines = db.relationship(
        "OrderLine",
        back_populates="order",
        cascade="all, delete-orphan",
        order_by="OrderLine.id",
    )
    items = synonym("order_lines")
    routing_steps = db.relationship(
        "RoutingStep",
        back_populates="order",
        cascade="all, delete-orphan",
        order_by="RoutingStep.sequence",
    )
    steps = synonym("routing_steps")
    gate_details = db.relationship(
        "GateOrderDetail",
        back_populates="order",
        cascade="all, delete-orphan",
        uselist=False,
    )

    def __repr__(self):
        return f"<Order {self.order_number} status={self.status}>"

    @property
    def primary_line(self):
        return self.order_lines[0] if self.order_lines else None

    @property
    def primary_item(self):
        return self.primary_line

    @property
    def routing_progress(self):
        if not self.routing_steps:
            return None
        completed = sum(1 for step in self.routing_steps if step.completed)
        total = len(self.routing_steps)
        if total == 0:
            return None
        return completed / total

    @property
    def status_label(self):
        if not self.status:
            return "—"
        return OrderStatus.LABELS.get(
            self.status, self.status.replace("_", " ").title()
        )


class OrderLine(db.Model):
    __tablename__ = "order_item"

    __table_args__ = (
        db.CheckConstraint(
            "(scheduled_start_date IS NULL) OR "
            "(scheduled_completion_date IS NULL) OR "
            "(scheduled_start_date <= scheduled_completion_date)",
            name="ck_order_line_schedule_window",
        ),
        db.CheckConstraint(
            "(promised_date IS NULL) OR (scheduled_completion_date IS NULL) OR "
            "(promised_date >= scheduled_completion_date)",
            name="ck_order_line_promised_after_completion",
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
    order_id = db.Column(db.Integer, db.ForeignKey("order.id"), nullable=False)
    item_id = db.Column(db.Integer, db.ForeignKey("item.id"), nullable=False)
    quantity = db.Column(db.Integer, nullable=False)
    promised_date = db.Column(db.Date, nullable=True)
    scheduled_start_date = db.Column(db.Date, nullable=True)
    scheduled_completion_date = db.Column(db.Date, nullable=True)

    order = db.relationship("Order", back_populates="order_lines")
    item = db.relationship("Item")
    components = db.relationship(
        "OrderComponent",
        back_populates="order_line",
        cascade="all, delete-orphan",
        order_by="OrderComponent.id",
    )
    bom_components = synonym("components")
    reservations = db.relationship(
        "Reservation",
        back_populates="order_line",
        cascade="all, delete-orphan",
        order_by="Reservation.id",
    )

    def __repr__(self):
        return (
            f"<OrderLine order={self.order_id} item={self.item_id} qty={self.quantity}>"
        )


class OrderComponent(db.Model):
    __tablename__ = "order_bom_component"

    __table_args__ = (
        db.UniqueConstraint(
            "order_item_id", "component_item_id", name="uq_order_component_item"
        ),
        db.CheckConstraint("quantity > 0", name="ck_order_component_quantity_positive"),
    )

    id = db.Column(db.Integer, primary_key=True)
    order_line_id = db.Column(
        "order_item_id", db.Integer, db.ForeignKey("order_item.id"), nullable=False
    )
    component_item_id = db.Column(db.Integer, db.ForeignKey("item.id"), nullable=False)
    quantity = db.Column(db.Numeric(10, 3), nullable=False)

    order_line = db.relationship("OrderLine", back_populates="components")
    order_item = synonym("order_line")
    component_item = db.relationship("Item")
    routing_step_links = db.relationship(
        "RoutingStepComponent",
        back_populates="order_component",
        cascade="all, delete-orphan",
        order_by="RoutingStepComponent.id",
    )
    step_usages = synonym("routing_step_links")
    routing_steps = association_proxy("routing_step_links", "routing_step")

    def __repr__(self):
        return (
            f"<OrderComponent order_line={self.order_line_id} "
            f"component={self.component_item_id} qty={self.quantity}>"
        )


class GateOrderDetail(db.Model):
    __tablename__ = "gate_order_detail"

    id = db.Column(db.Integer, primary_key=True)
    order_id = db.Column(db.Integer, db.ForeignKey("order.id"), nullable=False, unique=True)
    item_number = db.Column(db.String, nullable=False)
    production_quantity = db.Column(db.Integer, nullable=False)
    panel_count = db.Column(db.Integer, nullable=False)
    total_gate_height = db.Column(db.Numeric(10, 2), nullable=False)
    al_color = db.Column(db.String, nullable=False)
    insert_color = db.Column(db.String, nullable=False)
    lead_post_direction = db.Column(db.String, nullable=False)
    visi_panels = db.Column(db.String, nullable=False)
    half_panel_color = db.Column(db.String, nullable=False)
    hardware_option = db.Column(db.String, nullable=True)
    adders = db.Column(db.String, nullable=True)
    inspection_panel_count = db.Column(db.Integer, nullable=True)
    inspection_gate_height = db.Column(db.Numeric(10, 3), nullable=True)
    inspection_al_color = db.Column(db.String, nullable=True)
    inspection_insert_color = db.Column(db.String, nullable=True)
    inspection_lead_post_direction = db.Column(db.String, nullable=True)
    inspection_visi_panels = db.Column(db.String, nullable=True)
    inspection_recorded_at = db.Column(db.DateTime, nullable=True)

    order = db.relationship("Order", back_populates="gate_details")


class BillOfMaterial(db.Model):
    __tablename__ = "item_bom"

    __table_args__ = (
        db.UniqueConstraint("item_id", name="uq_bom_item"),
    )

    id = db.Column(db.Integer, primary_key=True)
    item_id = db.Column(db.Integer, db.ForeignKey("item.id"), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(
        db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )

    item = db.relationship("Item", backref=db.backref("bill_of_material", uselist=False))
    components = db.relationship(
        "BillOfMaterialComponent",
        back_populates="bom",
        cascade="all, delete-orphan",
        order_by="BillOfMaterialComponent.id",
    )

    def __repr__(self):
        return (
            f"<BillOfMaterial item={self.item_id} "
            f"components={len(self.components)}>"
        )


class BillOfMaterialComponent(db.Model):
    __tablename__ = "item_bom_component"

    __table_args__ = (
        db.UniqueConstraint(
            "bom_id", "component_item_id", name="uq_bom_component_item"
        ),
        db.CheckConstraint(
            "quantity > 0", name="ck_bom_component_quantity_positive"
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
    bom_id = db.Column(db.Integer, db.ForeignKey("item_bom.id"), nullable=False)
    component_item_id = db.Column(db.Integer, db.ForeignKey("item.id"), nullable=False)
    quantity = db.Column(db.Numeric(10, 3), nullable=False)

    bom = db.relationship("BillOfMaterial", back_populates="components")
    component_item = db.relationship("Item")

    def __repr__(self):
        return (
            f"<BillOfMaterialComponent bom={self.bom_id} "
            f"component={self.component_item_id} qty={self.quantity}>"
        )


class RoutingStep(db.Model):
    __tablename__ = "order_step"

    __table_args__ = (
        db.UniqueConstraint("order_id", "sequence", name="uq_routing_step_sequence"),
    )

    id = db.Column(db.Integer, primary_key=True)
    order_id = db.Column(db.Integer, db.ForeignKey("order.id"), nullable=False)
    sequence = db.Column(db.Integer, nullable=False)
    work_cell = db.Column(db.String, nullable=True)
    description = db.Column(db.String, nullable=False)
    completed = db.Column(db.Boolean, default=False, nullable=False)
    completed_at = db.Column(db.DateTime, nullable=True)

    order = db.relationship("Order", back_populates="routing_steps")
    component_links = db.relationship(
        "RoutingStepComponent",
        back_populates="routing_step",
        cascade="all, delete-orphan",
        order_by="RoutingStepComponent.id",
    )
    component_usages = synonym("component_links")
    components = association_proxy("component_links", "order_component")

    def __repr__(self):
        return (
            f"<RoutingStep order={self.order_id} seq={self.sequence} "
            f"completed={self.completed}>"
        )


class RoutingStepComponent(db.Model):
    __tablename__ = "order_step_component"

    __table_args__ = (
        db.UniqueConstraint(
            "order_step_id",
            "order_bom_component_id",
            name="uq_routing_step_component_usage",
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
    routing_step_id = db.Column(
        "order_step_id", db.Integer, db.ForeignKey("order_step.id"), nullable=False
    )
    order_component_id = db.Column(
        "order_bom_component_id",
        db.Integer,
        db.ForeignKey("order_bom_component.id"),
        nullable=False,
    )

    routing_step = db.relationship("RoutingStep", back_populates="component_links")
    order_component = db.relationship("OrderComponent", back_populates="routing_step_links")
    order_step = synonym("routing_step")
    bom_component = synonym("order_component")
    consumptions = db.relationship(
        "RoutingStepConsumption",
        back_populates="routing_step_component",
        cascade="all, delete-orphan",
        order_by="RoutingStepConsumption.id",
    )

    def __repr__(self):
        return (
            f"<RoutingStepComponent step={self.routing_step_id} "
            f"order_component={self.order_component_id}>"
        )


class RoutingStepConsumption(db.Model):
    __tablename__ = "order_step_consumption"

    id = db.Column(db.Integer, primary_key=True)
    routing_step_component_id = db.Column(
        db.Integer,
        db.ForeignKey("order_step_component.id"),
        nullable=False,
    )
    movement_id = db.Column(db.Integer, db.ForeignKey("movement.id"), nullable=False)
    quantity = db.Column(db.Numeric(12, 3), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    routing_step_component = db.relationship(
        "RoutingStepComponent", back_populates="consumptions"
    )
    movement = db.relationship("Movement")

    def __repr__(self):
        return (
            f"<RoutingStepConsumption usage={self.routing_step_component_id} "
            f"movement={self.movement_id} qty={self.quantity}>"
        )


class Reservation(db.Model):
    __tablename__ = "reservation"

    __table_args__ = (
        db.CheckConstraint("quantity > 0", name="ck_reservation_positive_quantity"),
    )

    id = db.Column(db.Integer, primary_key=True)
    order_line_id = db.Column(
        "order_item_id", db.Integer, db.ForeignKey("order_item.id"), nullable=False
    )
    item_id = db.Column(db.Integer, db.ForeignKey("item.id"), nullable=False)
    quantity = db.Column(db.Numeric(10, 3), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    order_line = db.relationship("OrderLine", back_populates="reservations")
    order_item = synonym("order_line")
    item = db.relationship("Item")

    def __repr__(self):
        return (
            f"<Reservation order_line={self.order_line_id} item={self.item_id} "
            f"qty={self.quantity}>"
        )


class OpenOrderSystemState:
    NEW = "NEW"
    OPEN = "OPEN"
    COMPLETED = "COMPLETED"
    REOPENED = "REOPENED"

    ACTIVE_STATES = {NEW, OPEN, REOPENED}
    ALL_STATES = [NEW, OPEN, COMPLETED, REOPENED]


class OpenOrderInternalStatus:
    UNREVIEWED = "UNREVIEWED"
    IN_PROGRESS = "IN_PROGRESS"
    NEEDS_FOLLOWUP = "NEEDS_FOLLOWUP"
    WAITING_CUSTOMER = "WAITING_CUSTOMER"
    DONE = "DONE"

    ALL_STATUSES = [
        UNREVIEWED,
        IN_PROGRESS,
        NEEDS_FOLLOWUP,
        WAITING_CUSTOMER,
        DONE,
    ]


class OpenOrderUpload(db.Model):
    __tablename__ = "open_order_upload"

    id = db.Column(db.Integer, primary_key=True)
    uploaded_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    uploaded_by_user_id = db.Column(
        db.Integer, db.ForeignKey("user.id", ondelete="SET NULL")
    )
    source_filename = db.Column(db.String(512), nullable=False)
    file_hash = db.Column(db.String(40), nullable=True)
    notes = db.Column(db.Text, nullable=True)

    uploaded_by = db.relationship(
        "User", backref=db.backref("open_order_uploads", lazy="dynamic")
    )

    __table_args__ = (db.Index("ix_open_order_upload_uploaded_at", "uploaded_at"),)


class OpenOrderLine(db.Model):
    __tablename__ = "open_order_line"

    id = db.Column(db.Integer, primary_key=True)
    natural_key = db.Column(db.String(128), nullable=False, unique=True, index=True)
    so_no = db.Column(db.String(64), nullable=False)
    so_state = db.Column(db.String(64), nullable=True)
    so_date = db.Column(db.Date, nullable=True)
    ship_by = db.Column(db.Date, nullable=True)
    customer_id = db.Column(db.String(64), nullable=True)
    customer_name = db.Column(db.String(255), nullable=True)
    item_id = db.Column(db.String(128), nullable=True)
    line_description = db.Column(db.Text, nullable=True)
    uom = db.Column(db.String(64), nullable=True)
    qty_ordered = db.Column(db.Numeric(12, 3), nullable=True)
    qty_shipped = db.Column(db.Numeric(12, 3), nullable=True)
    qty_remaining = db.Column(db.Numeric(12, 3), nullable=True)
    unit_price = db.Column(db.Numeric(12, 2), nullable=True)
    part_number = db.Column(db.String(128), nullable=True)

    system_state = db.Column(
        db.String(32), nullable=False, default=OpenOrderSystemState.NEW
    )
    first_seen_upload_id = db.Column(
        db.Integer, db.ForeignKey("open_order_upload.id", ondelete="SET NULL")
    )
    last_seen_upload_id = db.Column(
        db.Integer, db.ForeignKey("open_order_upload.id", ondelete="SET NULL")
    )
    completed_upload_id = db.Column(
        db.Integer, db.ForeignKey("open_order_upload.id", ondelete="SET NULL")
    )
    first_seen_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    last_seen_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    completed_at = db.Column(db.DateTime, nullable=True)

    internal_status = db.Column(
        db.String(64), nullable=False, default=OpenOrderInternalStatus.UNREVIEWED
    )
    owner_user_id = db.Column(db.Integer, db.ForeignKey("user.id", ondelete="SET NULL"))
    priority = db.Column(db.Integer, nullable=True)
    promised_date = db.Column(db.Date, nullable=True)
    notes = db.Column(db.Text, nullable=True)

    first_seen_upload = db.relationship(
        "OpenOrderUpload", foreign_keys=[first_seen_upload_id]
    )
    last_seen_upload = db.relationship(
        "OpenOrderUpload", foreign_keys=[last_seen_upload_id]
    )
    completed_upload = db.relationship(
        "OpenOrderUpload", foreign_keys=[completed_upload_id]
    )
    owner = db.relationship("User")

    __table_args__ = (
        db.Index("ix_open_order_line_system_state", "system_state"),
        db.Index("ix_open_order_line_customer_id", "customer_id"),
        db.Index("ix_open_order_line_so_no", "so_no"),
        db.Index("ix_open_order_line_item_id", "item_id"),
    )


class OpenOrderLineSnapshot(db.Model):
    __tablename__ = "open_order_line_snapshot"

    id = db.Column(db.Integer, primary_key=True)
    upload_id = db.Column(
        db.Integer, db.ForeignKey("open_order_upload.id", ondelete="CASCADE")
    )
    line_id = db.Column(
        db.Integer, db.ForeignKey("open_order_line.id", ondelete="CASCADE")
    )
    snapshot_json = db.Column(db.JSON, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    upload = db.relationship(
        "OpenOrderUpload",
        backref=db.backref("line_snapshots", lazy="dynamic", cascade="all, delete-orphan"),
    )
    line = db.relationship(
        "OpenOrderLine",
        backref=db.backref("snapshots", lazy="dynamic", cascade="all, delete-orphan"),
    )


# Backwards compatibility aliases for legacy imports
OrderItem = OrderLine
OrderBOMComponent = OrderComponent
OrderStep = RoutingStep
OrderStepComponent = RoutingStepComponent
