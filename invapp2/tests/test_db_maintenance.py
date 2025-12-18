import sys
from pathlib import Path

from sqlalchemy import Column, Integer, String, create_engine
from sqlalchemy.orm import Session, declarative_base

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

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
