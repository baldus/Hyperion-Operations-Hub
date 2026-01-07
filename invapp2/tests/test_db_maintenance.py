import sys
from pathlib import Path

from sqlalchemy import Column, Integer, String, create_engine
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, declarative_base

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import invapp.db_maintenance as db_maintenance
from invapp.db_maintenance import analyze_primary_key_sequences, repair_primary_key_sequences


Base = declarative_base()


class TempRecord(Base):
    __tablename__ = "temp_record"
    __table_args__ = {"sqlite_autoincrement": True}

    id = Column(Integer, primary_key=True)
    name = Column(String)


def _setup_engine():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return engine


def test_empty_table_resets_sequence_to_one():
    engine = _setup_engine()

    summary = repair_primary_key_sequences(engine, Base, models=[TempRecord])
    checks = analyze_primary_key_sequences(engine, Base, models=[TempRecord])

    assert summary["repaired"] == 1
    assert checks[0].desired_next_value == 1
    assert checks[0].status == "ok"
    assert checks[0].current_next_value == 1


def test_sequence_moves_past_existing_rows():
    engine = _setup_engine()

    with Session(engine) as session:
        session.add_all([TempRecord(id=5, name="first"), TempRecord(id=9, name="second")])
        session.commit()

    summary = repair_primary_key_sequences(engine, Base, models=[TempRecord])
    checks = analyze_primary_key_sequences(engine, Base, models=[TempRecord])

    assert summary["repaired"] == 1
    assert checks[0].desired_next_value == 10
    assert checks[0].current_next_value == 10
    assert checks[0].status == "ok"


def test_postgres_repairs_continue_after_failure(monkeypatch):
    class FirstModel(Base):
        __tablename__ = "first_table"

        id = Column(Integer, primary_key=True)

    class SecondModel(Base):
        __tablename__ = "second_table"

        id = Column(Integer, primary_key=True)

    class FakeInspector:
        def get_columns(self, table_name, schema=None):
            return [{"name": "id"}]

    class FakeTransaction:
        def __init__(self, connection):
            self.connection = connection
            self.is_active = True

        def commit(self):
            self.connection.commits += 1
            self.is_active = False

        def rollback(self):
            self.connection.rollbacks += 1
            self.is_active = False

    class FakeResult:
        def __init__(self, scalar=None, row=None):
            self._scalar = scalar
            self._row = row

        def scalar_one_or_none(self):
            return self._scalar

        def scalar_one(self):
            return self._scalar

        def scalar(self):
            return self._scalar

        def fetchone(self):
            return self._row

    class FakeConnection:
        def __init__(self):
            self.rollbacks = 0
            self.commits = 0
            self.setval_calls = []
            self.fail_first_setval = True

        def begin(self):
            return FakeTransaction(self)

        def execute(self, query, params=None):
            text_value = getattr(query, "text", "")
            if "pg_get_serial_sequence" in text_value:
                table_name = params["table_name"]
                return FakeResult(f"{table_name}_id_seq")
            if "setval" in text_value:
                self.setval_calls.append(params["sequence_name"])
                if self.fail_first_setval:
                    self.fail_first_setval = False
                    raise SQLAlchemyError("boom")
                return FakeResult()
            if "last_value" in text_value:
                return FakeResult(row=(1, 1))
            return FakeResult(0)

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    class FakeDialect:
        name = "postgresql"

        class identifier_preparer:
            @staticmethod
            def quote(value):
                return f"\"{value}\""

    class FakeEngine:
        dialect = FakeDialect()

        def __init__(self):
            self.connection = FakeConnection()

        def connect(self):
            return self.connection

    monkeypatch.setattr(db_maintenance, "inspect", lambda engine: FakeInspector())

    engine = FakeEngine()
    summary = repair_primary_key_sequences(engine, Base, models=[FirstModel, SecondModel])

    assert summary["failed"] == 1
    assert summary["repaired"] == 1
    assert engine.connection.rollbacks == 1
    assert engine.connection.commits == 1
    assert len(engine.connection.setval_calls) == 2


def test_db_maintenance_does_not_query_pg_sequences():
    source = Path(db_maintenance.__file__).read_text()
    assert "pg_sequences" not in source
