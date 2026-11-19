"""Database workflows against a temporary SQLite file. All data is synthetic."""

import hashlib
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from sqlalchemy import Engine, func, select, text
from sqlalchemy.exc import IntegrityError

from jobsearch.config import DatabaseConfig, KeywordBaselineConfig
from jobsearch.database import make_engine, session_scope
from jobsearch.models.job import CompanySource, FetchStatus, JobFetchRecord
from jobsearch.models.tables import (
    Base,
    FitPrediction,
    HumanLabel,
    Job,
    JobFetchAttempt,
    ResumeVersion,
)
from jobsearch.services.job_import import import_records, read_records, set_job_company
from jobsearch.services.migrations import (
    SchemaOutOfDateError,
    check_schema_current,
    upgrade_database,
)
from jobsearch.services.profiles import ParsedResume, save_resume_version
from jobsearch.services.scoring.keyword_baseline import run_keyword_baseline


@pytest.fixture
def database(tmp_path: Path) -> DatabaseConfig:
    return DatabaseConfig(path=tmp_path / "nested" / "test.db")


@pytest.fixture
def engine(database: DatabaseConfig) -> Iterator[Engine]:
    upgrade_database(database)
    engine = make_engine(database)
    yield engine
    engine.dispose()


def record(
    *,
    run_id: str = "run-1",
    key: str = "greenhouse:examplecorp:1",
    status: FetchStatus = FetchStatus.FETCHED,
    description: str | None = "Build services.",
    company: str | None = "Example Corp",
    day: int = 1,
) -> JobFetchRecord:
    fetched = status is FetchStatus.FETCHED
    return JobFetchRecord(
        run_id=run_id,
        source_url=f"https://job-boards.greenhouse.io/examplecorp/jobs/{key.rsplit(':', 1)[1]}",
        job_key=key,
        source="greenhouse",
        status=status,
        retrieved_at=datetime(2026, 1, day, 12, tzinfo=UTC),
        title="Software Engineer" if fetched else None,
        company=company if fetched else None,
        description_text=description if fetched else None,
        detail=None if fetched else "closed",
    )


def counts(engine: Engine) -> tuple[int, int, int]:
    with session_scope(engine) as session:
        return (
            session.scalar(select(func.count()).select_from(Job)) or 0,
            session.scalar(select(func.count(Job.description_text))) or 0,
            session.scalar(select(func.count()).select_from(JobFetchAttempt)) or 0,
        )


def test_migrations_match_the_models(engine: Engine) -> None:
    with engine.connect() as connection:
        diff = compare_metadata(MigrationContext.configure(connection), Base.metadata)
    assert diff == []


def test_connections_use_wal_busy_timeout_and_foreign_keys(engine: Engine) -> None:
    with engine.connect() as connection:
        assert connection.execute(text("PRAGMA journal_mode")).scalar() == "wal"
        assert connection.execute(text("PRAGMA busy_timeout")).scalar() == 5000
        assert connection.execute(text("PRAGMA foreign_keys")).scalar() == 1


def test_schema_check_requires_upgrade(database: DatabaseConfig) -> None:
    engine = make_engine(database)
    with pytest.raises(SchemaOutOfDateError):
        check_schema_current(engine)
    upgrade_database(database)
    check_schema_current(engine)
    engine.dispose()


def test_import_is_idempotent(engine: Engine) -> None:
    records = [record(), record(key="greenhouse:examplecorp:2", status=FetchStatus.UNAVAILABLE)]
    with session_scope(engine) as session:
        first = import_records(session, records, applied=True)
    with session_scope(engine) as session:
        second = import_records(session, records, applied=True)
    assert (first.jobs_created, first.descriptions_added, first.attempts_created) == (2, 1, 2)
    assert (second.jobs_created, second.descriptions_added, second.attempts_created) == (0, 0, 0)
    assert counts(engine) == (2, 1, 2)


def test_changed_description_keeps_the_first_and_records_the_new_hash(engine: Engine) -> None:
    first = record()
    changed = record(run_id="run-2", description="Build and run services.", day=2)
    with session_scope(engine) as session:
        import_records(session, [first], applied=True)
    with session_scope(engine) as session:
        summary = import_records(session, [changed], applied=True)
    assert summary.descriptions_differed == 1
    assert counts(engine) == (1, 1, 2)
    with engine.connect() as connection:
        stored = connection.execute(
            text("SELECT description_text, last_status FROM jobs_overview")
        ).one()
        hashes = connection.execute(
            text("SELECT description_hash FROM job_fetch_attempts ORDER BY attempted_at")
        ).scalars()
        assert list(hashes) == [first.description_hash, changed.description_hash]
    assert tuple(stored) == ("Build services.", "fetched")


def test_unchanged_description_in_a_later_run_adds_only_an_attempt(engine: Engine) -> None:
    with session_scope(engine) as session:
        summary = import_records(session, [record(), record(run_id="run-2", day=2)], applied=True)
    assert summary.descriptions_differed == 0
    assert counts(engine) == (1, 1, 2)


def test_later_fetch_fills_a_description_an_earlier_fetch_missed(engine: Engine) -> None:
    with session_scope(engine) as session:
        import_records(session, [record(status=FetchStatus.FAILED)], applied=True)
    assert counts(engine) == (1, 0, 1)
    with session_scope(engine) as session:
        import_records(session, [record(run_id="run-2", day=2)], applied=True)
    assert counts(engine) == (1, 1, 2)


def test_same_url_with_a_better_key_updates_the_job_instead_of_adding_one(engine: Engine) -> None:
    page = record(key="page:1", status=FetchStatus.NO_DETAILS)
    with session_scope(engine) as session:
        import_records(session, [page], applied=True)
    fetched = record(run_id="run-2", key="greenhouse:examplecorp:1", day=2).model_copy(
        update={"source_url": page.source_url}
    )
    with session_scope(engine) as session:
        import_records(session, [fetched], applied=True)
    assert counts(engine) == (1, 1, 2)
    failed_again = record(run_id="run-3", key="page:1", status=FetchStatus.FAILED, day=3)
    with session_scope(engine) as session:
        import_records(session, [failed_again], applied=True)
    assert counts(engine) == (1, 1, 3)
    with session_scope(engine) as session:
        assert session.scalars(select(Job)).one().job_key == "greenhouse:examplecorp:1"


def test_overview_shows_jobs_that_could_not_be_fetched(engine: Engine) -> None:
    with session_scope(engine) as session:
        import_records(session, [record(status=FetchStatus.UNAVAILABLE)], applied=True)
    with engine.connect() as connection:
        row = connection.execute(
            text("SELECT title, last_status, last_detail, applied FROM jobs_overview")
        ).one()
    assert tuple(row) == (None, "unavailable", "closed", 1)


def test_failed_import_rolls_back(engine: Engine, tmp_path: Path) -> None:
    jsonl = tmp_path / "jobs.jsonl"
    jsonl.write_text(record().model_dump_json() + "\n{not json}\n", encoding="utf-8")
    with pytest.raises(ValueError, match=r"jobs\.jsonl:2"), session_scope(engine) as session:
        import_records(session, read_records(jsonl), applied=True)
    assert counts(engine) == (0, 0, 0)


def test_later_fetch_fills_a_missing_company_but_never_changes_one(engine: Engine) -> None:
    with session_scope(engine) as session:
        import_records(session, [record(company=None)], applied=True)
    with session_scope(engine) as session:
        filled = import_records(session, [record(run_id="run-2", day=2)], applied=True)
    with session_scope(engine) as session:
        import_records(session, [record(run_id="run-3", company="Other Name", day=3)], applied=True)
    assert filled.job_companies_updated == 1
    assert counts(engine) == (1, 1, 3)
    assert company_row(engine) == ("Example Corp", "job_posting")


def company_row(engine: Engine) -> tuple[object, ...]:
    with engine.connect() as connection:
        return tuple(connection.execute(text("SELECT company, company_source FROM jobs")).one())


def hinted(run_id: str, company: str, source: CompanySource, day: int) -> JobFetchRecord:
    return record(run_id=run_id, status=FetchStatus.UNAVAILABLE, day=day).model_copy(
        update={"company": company, "company_source": source}
    )


def test_better_company_source_replaces_a_weaker_one_but_not_the_reverse(engine: Engine) -> None:
    with session_scope(engine) as session:
        import_records(session, [hinted("r1", "examplecorp", CompanySource.URL, 1)], applied=True)
    assert company_row(engine) == ("examplecorp", "url")
    with session_scope(engine) as session:
        import_records(
            session, [hinted("r2", "Example Corp", CompanySource.JOB_BOARD, 2)], applied=True
        )
    assert company_row(engine) == ("Example Corp", "job_board")
    with session_scope(engine) as session:
        import_records(session, [hinted("r3", "examplecorp", CompanySource.URL, 3)], applied=True)
    assert company_row(engine) == ("Example Corp", "job_board")


def test_user_company_is_never_replaced_automatically(engine: Engine) -> None:
    with session_scope(engine) as session:
        import_records(session, [record(status=FetchStatus.UNAVAILABLE)], applied=True)
    with session_scope(engine) as session:
        job = session.scalars(select(Job)).one()
        assert set_job_company(job, "My Name For It", CompanySource.USER)
    with session_scope(engine) as session:
        import_records(session, [record(run_id="run-2", day=2)], applied=True)
    assert company_row(engine) == ("My Name For It", "user")
    with engine.connect() as connection:
        assert connection.execute(text("SELECT company FROM jobs_overview")).scalar() == (
            "My Name For It"
        )


def test_overview_shows_company_for_unfetched_jobs(engine: Engine) -> None:
    with session_scope(engine) as session:
        import_records(
            session, [hinted("r1", "Example Corp", CompanySource.JOB_BOARD, 1)], applied=True
        )
    with engine.connect() as connection:
        row = connection.execute(
            text("SELECT title, company, company_source, last_status FROM jobs_overview")
        ).one()
    assert tuple(row) == (None, "Example Corp", "job_board", "unavailable")


def test_upgrade_from_0001_backfills_company_from_versions(database: DatabaseConfig) -> None:
    upgrade_database(database, "0001")
    engine = make_engine(database)
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO jobs (id, job_key, source, source_url, applied, first_seen_at) "
                "VALUES (1, 'k1', 'greenhouse', 'u1', 1, '2026-01-01 00:00:00'), "
                "(2, 'k2', 'greenhouse', 'u2', 1, '2026-01-01 00:00:00')"
            )
        )
        connection.execute(
            text(
                "INSERT INTO job_versions (job_id, description_hash, title, company, "
                "description_text, source_url, retrieved_at, run_id) VALUES "
                "(1, 'h1', 'T', 'Old Name', 'D1', 'u1', '2026-01-01 00:00:00', 'r1'), "
                "(1, 'h2', 'T', 'New Name', 'D2', 'u1', '2026-01-02 00:00:00', 'r2')"
            )
        )
    upgrade_database(database, "0003")
    with engine.connect() as connection:
        rows = connection.execute(
            text("SELECT job_id, company, company_source FROM jobs_overview ORDER BY job_id")
        ).all()
    engine.dispose()
    assert [tuple(row) for row in rows] == [(1, "New Name", "job_posting"), (2, None, None)]


def _insert_at_0003(engine: Engine, *, second_version: bool = False) -> None:
    extra = ", (8, 1, 'h2', 'T', NULL, 'D2', NULL, 'u1', '2026-01-02 00:00:00', 'r2')"
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO jobs (id, job_key, source, source_url, applied, first_seen_at) "
                "VALUES (1, 'k1', 'greenhouse', 'u1', 1, '2026-01-01 00:00:00'), "
                "(2, 'k2', 'greenhouse', 'u2', 1, '2026-01-01 00:00:00')"
            )
        )
        connection.execute(
            text(
                "INSERT INTO job_versions (id, job_id, description_hash, title, company, "
                "description_text, posting_url, source_url, retrieved_at, run_id) VALUES "
                "(7, 1, 'h1', 'T', NULL, 'D1', 'p1', 'u1', '2026-01-01 00:00:00', 'r1')"
                + (extra if second_version else "")
            )
        )
        connection.execute(
            text(
                "INSERT INTO job_fetch_attempts (job_id, job_version_id, run_id, status, "
                "source_url, attempted_at) VALUES "
                "(1, 7, 'r1', 'fetched', 'u1', '2026-01-01 00:00:00'), "
                "(2, NULL, 'r1', 'failed', 'u2', '2026-01-01 00:00:00')"
            )
        )
        connection.execute(
            text(
                "INSERT INTO fit_predictions (job_id, job_version_id, fit_score, provider, "
                "model_id, prompt_version, rubric_version, resume_version, run_id, created_at) "
                "VALUES (1, 7, 4, 'p', 'm', 'pv', 'rv', 'r', 'r1', '2026-01-03 00:00:00')"
            )
        )


def test_upgrade_to_0004_moves_descriptions_and_downgrade_restores_versions(
    database: DatabaseConfig,
) -> None:
    from alembic import command
    from alembic.config import Config

    upgrade_database(database, "0003")
    engine = make_engine(database)
    _insert_at_0003(engine)
    upgrade_database(database)
    overview = (
        "SELECT job_id, title, description_text, posting_url, ai_fit_score "
        "FROM jobs_overview ORDER BY job_id"
    )
    with engine.connect() as connection:
        rows = [tuple(row) for row in connection.execute(text(overview))]
        hashes = connection.execute(
            text("SELECT description_hash FROM job_fetch_attempts ORDER BY job_id")
        ).scalars()
        assert list(hashes) == ["h1", None]
    assert rows == [(1, "T", "D1", "p1", 4), (2, None, None, None, None)]

    config = Config("alembic.ini")
    config.attributes["database"] = database
    command.downgrade(config, "0003")
    with engine.connect() as connection:
        assert [tuple(row) for row in connection.execute(text(overview))] == rows
        assert connection.execute(text("SELECT run_id FROM job_versions")).scalar() == "r1"
        linked = "SELECT job_version_id FROM {} ORDER BY job_id"
        versions = connection.execute(text(linked.format("job_fetch_attempts"))).scalars()
        assert list(versions) == [1, None]
        assert connection.execute(text(linked.format("fit_predictions"))).scalar() == 1
    engine.dispose()


def test_upgrade_to_0004_refuses_jobs_with_several_versions(database: DatabaseConfig) -> None:
    upgrade_database(database, "0003")
    engine = make_engine(database)
    _insert_at_0003(engine, second_version=True)
    with pytest.raises(RuntimeError, match="more than one description version"):
        upgrade_database(database)
    with engine.connect() as connection:
        assert connection.execute(text("SELECT COUNT(*) FROM job_versions")).scalar() == 2
    engine.dispose()


def test_downgrade_to_0001_restores_the_original_view(database: DatabaseConfig) -> None:
    from alembic import command
    from alembic.config import Config

    upgrade_database(database)
    config = Config("alembic.ini")
    config.attributes["database"] = database
    command.downgrade(config, "0001")
    engine = make_engine(database)
    with engine.connect() as connection:
        columns = [row[1] for row in connection.execute(text("PRAGMA table_info(jobs)"))]
        connection.execute(text("SELECT company FROM jobs_overview")).all()
    engine.dispose()
    assert "company" not in columns


def _fetched_job(engine: Engine) -> int:
    with session_scope(engine) as session:
        import_records(session, [record()], applied=True)
    with session_scope(engine) as session:
        return session.scalars(select(Job)).one().id


def _label(job_id: int, fit: int, apply: bool, day: int) -> HumanLabel:
    return HumanLabel(
        job_id=job_id, fit_score=fit, would_apply=apply,
        rubric_version="rubric_v1", labeled_at=datetime(2026, 2, day, tzinfo=UTC),
    )  # fmt: skip


def _prediction(job_id: int, fit: int, day: int) -> FitPrediction:
    return FitPrediction(
        job_id=job_id, fit_score=fit, provider="example",
        model_id="example-model", prompt_version="fit_score_v1", rubric_version="rubric_v1",
        resume_version="resume-1", run_id=f"run-{day}",
        created_at=datetime(2026, 2, day, tzinfo=UTC),
    )  # fmt: skip


def scores(engine: Engine) -> tuple[object, ...]:
    with engine.connect() as connection:
        return tuple(
            connection.execute(
                text("SELECT human_fit_score, human_would_apply, ai_fit_score FROM jobs_overview")
            ).one()
        )


def test_overview_scores_are_empty_until_labeled_or_scored(engine: Engine) -> None:
    _fetched_job(engine)
    assert scores(engine) == (None, None, None)


def test_overview_shows_latest_human_label_and_prediction(engine: Engine) -> None:
    job_id = _fetched_job(engine)
    with session_scope(engine) as session:
        session.add_all(
            [
                _label(job_id, 4, True, 1),
                _label(job_id, 3, False, 2),  # re-scoring pass
                _prediction(job_id, 5, 1),
                _prediction(job_id, 2, 3),
            ]
        )
    assert scores(engine) == (3, 0, 2)
    with session_scope(engine) as session:
        assert session.scalar(select(func.count()).select_from(HumanLabel)) == 2


def test_upgrade_to_0005_keeps_labels_and_downgrade_removes_notes(
    database: DatabaseConfig,
) -> None:
    from alembic import command
    from alembic.config import Config

    upgrade_database(database, "0004")
    engine = make_engine(database)
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO jobs (id, job_key, source, source_url, applied, first_seen_at) "
                "VALUES (1, 'k1', 'greenhouse', 'u1', 1, '2026-01-01 00:00:00')"
            )
        )
        connection.execute(
            text(
                "INSERT INTO human_labels (job_id, fit_score, would_apply, rubric_version, "
                "labeled_at) VALUES (1, 4, 1, 'v1', '2026-02-01 00:00:00')"
            )
        )
    upgrade_database(database)
    with session_scope(engine) as session:
        label = session.scalars(select(HumanLabel)).one()
        assert (label.fit_score, label.notes) == (4, None)
        label.notes = "Strong backend match."
    with session_scope(engine) as session:
        assert session.scalars(select(HumanLabel)).one().notes == "Strong backend match."

    config = Config("alembic.ini")
    config.attributes["database"] = database
    command.downgrade(config, "0004")
    with engine.connect() as connection:
        columns = [row[1] for row in connection.execute(text("PRAGMA table_info(human_labels)"))]
        assert connection.execute(text("SELECT fit_score FROM human_labels")).scalar() == 4
    engine.dispose()
    assert "notes" not in columns


def test_upgrade_to_0006_defaults_got_interviewed_to_false_and_downgrade_removes_it(
    database: DatabaseConfig,
) -> None:
    from alembic import command
    from alembic.config import Config

    upgrade_database(database, "0005")
    engine = make_engine(database)
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO jobs (id, job_key, source, source_url, applied, first_seen_at) "
                "VALUES (1, 'k1', 'greenhouse', 'u1', 1, '2026-01-01 00:00:00')"
            )
        )
    upgrade_database(database)
    with engine.begin() as connection:
        # A row inserted without the column, as from sqlitebrowser, also gets the default.
        connection.execute(
            text(
                "INSERT INTO jobs (id, job_key, source, source_url, applied, first_seen_at) "
                "VALUES (2, 'k2', 'greenhouse', 'u2', 1, '2026-01-01 00:00:00')"
            )
        )
    with session_scope(engine) as session:
        jobs = session.scalars(select(Job).order_by(Job.id)).all()
        assert [job.got_interviewed for job in jobs] == [False, False]
        jobs[0].got_interviewed = True
    with session_scope(engine) as session:
        assert session.get(Job, 1).got_interviewed is True  # type: ignore[union-attr]
    with pytest.raises(IntegrityError), engine.begin() as connection:
        connection.execute(text("UPDATE jobs SET got_interviewed = NULL"))

    config = Config("alembic.ini")
    config.attributes["database"] = database
    command.downgrade(config, "0005")
    with engine.connect() as connection:
        columns = [row[1] for row in connection.execute(text("PRAGMA table_info(jobs)"))]
        assert connection.execute(text("SELECT job_key FROM jobs")).scalar() == "k1"
    engine.dispose()
    assert "got_interviewed" not in columns


def test_label_without_fetched_text_is_allowed(engine: Engine) -> None:
    with session_scope(engine) as session:
        import_records(session, [record(status=FetchStatus.UNAVAILABLE)], applied=True)
    with session_scope(engine) as session:
        job = session.scalars(select(Job)).one()
        session.add(_label(job.id, 2, False, 1))
    assert scores(engine) == (2, 0, None)


@pytest.mark.parametrize("fit", [0, 6])
def test_scores_must_be_on_the_1_to_5_scale(engine: Engine, fit: int) -> None:
    job_id = _fetched_job(engine)
    for row in (_label(job_id, fit, True, 1), _prediction(job_id, fit, 1)):
        with pytest.raises(IntegrityError), session_scope(engine) as session:
            session.add(row)


def test_upgrade_from_0002_keeps_jobs_and_downgrade_restores_view(
    database: DatabaseConfig,
) -> None:
    from alembic import command
    from alembic.config import Config

    upgrade_database(database, "0002")
    engine = make_engine(database)
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO jobs (id, job_key, source, source_url, applied, first_seen_at, "
                "company, company_source) VALUES (1, 'k1', 'greenhouse', 'u1', 1, "
                "'2026-01-01 00:00:00', 'Example Corp', 'job_posting')"
            )
        )
        connection.execute(
            text(
                "INSERT INTO job_versions (job_id, description_hash, title, description_text, "
                "source_url, retrieved_at, run_id) VALUES "
                "(1, 'h1', 'Software Engineer', 'D1', 'u1', '2026-01-01 00:00:00', 'r1')"
            )
        )
    upgrade_database(database)
    assert scores(engine) == (None, None, None)
    config = Config("alembic.ini")
    config.attributes["database"] = database
    command.downgrade(config, "0002")
    with engine.connect() as connection:
        row = connection.execute(text("SELECT title, company FROM jobs_overview")).one()
        tables = {r[0] for r in connection.execute(text("SELECT name FROM sqlite_master"))}
    engine.dispose()
    assert tuple(row) == ("Software Engineer", "Example Corp")
    assert "human_labels" not in tables and "fit_predictions" not in tables


def _alembic_config(database: DatabaseConfig) -> Config:

    config = Config("alembic.ini")
    config.attributes["database"] = database
    return config


def test_failed_migration_rolls_back_its_ddl(
    database: DatabaseConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    from alembic.operations import Operations

    upgrade_database(database, "0001")

    def fail(*args: object, **kwargs: object) -> None:
        raise RuntimeError("injected failure after DROP VIEW")

    # 0002 drops jobs_overview, then alters jobs: fail at the alter.
    monkeypatch.setattr(Operations, "batch_alter_table", fail)
    with pytest.raises(RuntimeError, match="injected failure"):
        upgrade_database(database, "0002")
    engine = make_engine(database)
    with engine.connect() as connection:
        view = connection.execute(
            text("SELECT sql FROM sqlite_master WHERE type = 'view' AND name = 'jobs_overview'")
        ).scalar()
        revision = connection.execute(text("SELECT version_num FROM alembic_version")).scalar()
        columns = [row[1] for row in connection.execute(text("PRAGMA table_info(jobs)"))]
    engine.dispose()
    assert view is not None and "v.company" in view
    assert revision == "0001"
    assert "company" not in columns


def test_each_migration_commits_on_its_own(
    database: DatabaseConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    from alembic.operations import Operations

    upgrade_database(database, "0001")
    original = Operations.batch_alter_table
    calls = []

    def fail_after_0002(self: Operations, *args: object, **kwargs: object) -> object:
        calls.append(args)
        if len(calls) > 1:  # 0002 alters jobs once; the next alter belongs to 0003.
            raise RuntimeError("injected failure in 0003")
        return original(self, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(Operations, "batch_alter_table", fail_after_0002)
    with pytest.raises(RuntimeError, match="injected failure"):
        upgrade_database(database, "0003")
    engine = make_engine(database)
    with engine.connect() as connection:
        revision = connection.execute(text("SELECT version_num FROM alembic_version")).scalar()
        tables = {r[0] for r in connection.execute(text("SELECT name FROM sqlite_master"))}
    engine.dispose()
    assert revision == "0002"
    assert "human_labels" not in tables


def test_downgrade_to_0001_keeps_populated_jobs(database: DatabaseConfig) -> None:
    from alembic import command

    upgrade_database(database, "0003")
    engine = make_engine(database)
    _insert_at_0003(engine)
    upgrade_database(database)
    command.downgrade(_alembic_config(database), "0001")
    with engine.connect() as connection:
        jobs = connection.execute(text("SELECT id, job_key FROM jobs ORDER BY id")).all()
        versions = connection.execute(text("SELECT job_id, title FROM job_versions")).all()
        attempts = connection.execute(
            text("SELECT job_id, job_version_id FROM job_fetch_attempts ORDER BY job_id")
        ).all()
        overview = connection.execute(
            text("SELECT job_id, title FROM jobs_overview ORDER BY job_id")
        ).all()
        problems = connection.execute(text("PRAGMA foreign_key_check")).all()
    engine.dispose()
    assert [tuple(r) for r in jobs] == [(1, "k1"), (2, "k2")]
    assert [tuple(r) for r in versions] == [(1, "T")]
    assert [tuple(r) for r in attempts] == [(1, 1), (2, None)]
    assert [tuple(r) for r in overview] == [(1, "T"), (2, None)]
    assert problems == []


def test_write_sessions_take_the_write_lock_before_reading(
    database: DatabaseConfig, engine: Engine
) -> None:
    from sqlalchemy.exc import OperationalError

    impatient = make_engine(database.model_copy(update={"busy_timeout_ms": 1}))
    with session_scope(engine) as first:
        first.scalar(select(func.count()).select_from(Job))  # a read, before any write
        # A second writer can't start, so it can't act on what the first one read...
        with pytest.raises(OperationalError, match="locked"), session_scope(impatient) as second:
            second.scalar(select(func.count()).select_from(Job))
        # ...while plain reads still work alongside it (WAL).
        with impatient.connect() as reader:
            assert reader.execute(select(func.count()).select_from(Job)).scalar() == 0
    with session_scope(impatient) as second:  # free again once the first commits
        assert second.scalar(select(func.count()).select_from(Job)) == 0
    impatient.dispose()


def test_database_path_may_contain_url_characters(tmp_path: Path) -> None:
    database = DatabaseConfig(path=tmp_path / "odd?mode=ro#1.db")
    upgrade_database(database)
    assert sorted(p.name for p in tmp_path.iterdir() if p.suffix == ".db") == ["odd?mode=ro#1.db"]
    engine = make_engine(database)
    check_schema_current(engine)
    engine.dispose()


def test_restore_from_backup(database: DatabaseConfig, engine: Engine, tmp_path: Path) -> None:
    """The README's backup and restore steps, through the same SQLite online backup API
    that the sqlite3 CLI's `.backup` and `.restore` use."""
    import sqlite3
    from contextlib import closing

    def copy(source: Path, target: Path) -> None:
        with closing(sqlite3.connect(source)) as src, closing(sqlite3.connect(target)) as dst:
            src.backup(dst)

    with session_scope(engine) as session:
        import_records(session, [record()], applied=True)
    backup = tmp_path / "jobsearch-backup.db"
    copy(database.path, backup)
    with session_scope(engine) as session:
        import_records(
            session, [record(run_id="run-2", key="greenhouse:examplecorp:2")], applied=True
        )
    assert counts(engine)[0] == 2
    engine.dispose()  # step 2: nothing else has the database open

    with closing(sqlite3.connect(backup)) as check:  # step 3: check the backup first
        assert check.execute("PRAGMA integrity_check").fetchone() == ("ok",)
    copy(backup, database.path)  # step 4: restore

    upgrade_database(database)  # step 5: verify
    restored = make_engine(database)
    check_schema_current(restored)
    with restored.connect() as connection:
        assert connection.execute(text("PRAGMA integrity_check")).scalar() == "ok"
    assert counts(restored) == (1, 1, 1)
    restored.dispose()


def _resume(text: str, filename: str = "resume.pdf") -> ParsedResume:
    return ParsedResume(
        text=text,
        text_hash=hashlib.sha256(text.encode()).hexdigest(),
        file_hash=hashlib.sha256(filename.encode() + text.encode()).hexdigest(),
        page_count=1,
        source_filename=filename,
    )


def test_loading_the_same_resume_text_again_adds_no_version(engine: Engine) -> None:
    with session_scope(engine) as session:
        first, created = save_resume_version(session, _resume("Jane Doe\nPython"))
        assert created
    with session_scope(engine) as session:
        again, created = save_resume_version(session, _resume("Jane Doe\nPython", "renamed.pdf"))
        assert not created
        assert again.id == first.id
        assert again.source_filename == "resume.pdf"


def test_changed_resume_text_adds_a_version_and_keeps_the_old_one(engine: Engine) -> None:
    with session_scope(engine) as session:
        save_resume_version(session, _resume("Jane Doe\nPython"))
        save_resume_version(session, _resume("Jane Doe\nPython, Go"))
    with session_scope(engine) as session:
        texts = session.scalars(select(ResumeVersion.text).order_by(ResumeVersion.id)).all()
    assert texts == ["Jane Doe\nPython", "Jane Doe\nPython, Go"]


def test_upgrade_to_0007_adds_resume_versions_and_downgrade_removes_it(
    database: DatabaseConfig,
) -> None:
    from alembic import command
    from alembic.config import Config

    upgrade_database(database, "0006")
    upgrade_database(database)
    engine = make_engine(database)
    with session_scope(engine) as session:
        save_resume_version(session, _resume("Jane Doe"))

    config = Config("alembic.ini")
    config.attributes["database"] = database
    command.downgrade(config, "0006")
    with engine.connect() as connection:
        tables = {row[0] for row in connection.execute(text("SELECT name FROM sqlite_master"))}
    engine.dispose()
    assert "resume_versions" not in tables
    assert "jobs" in tables


def _baseline_setup(engine: Engine) -> ResumeVersion:
    with session_scope(engine) as session:
        import_records(
            session,
            [
                record(key="greenhouse:examplecorp:1", description="Python and Kubernetes."),
                record(key="greenhouse:examplecorp:2", description="Go and Rust services."),
                record(key="greenhouse:examplecorp:3", description="Friendly team, good snacks."),
                record(key="greenhouse:examplecorp:4", status=FetchStatus.UNAVAILABLE),
            ],
            applied=True,
        )
        resume, _ = save_resume_version(session, _resume("Jane Doe\nPython, Kubernetes, Go"))
        return resume


def test_keyword_baseline_stores_scores_with_versions_and_details(engine: Engine) -> None:
    resume = _baseline_setup(engine)
    with session_scope(engine) as session:
        summary = run_keyword_baseline(
            session, resume, KeywordBaselineConfig(), "rubric_v1", "run-a"
        )
    assert summary.jobs_scored == 2
    assert summary.jobs_without_skills == [3]
    with session_scope(engine) as session:
        predictions = session.scalars(select(FitPrediction).order_by(FitPrediction.job_id)).all()
        assert [(p.job_id, p.fit_score) for p in predictions] == [(1, 5), (2, 3)]
        first = predictions[0]
        assert (first.provider, first.model_id, first.prompt_version) == (
            "baseline",
            "keyword_overlap",
            "keyword_skills_v1",
        )
        assert first.resume_version == resume.text_hash
        assert first.rubric_version == "rubric_v1"
        assert predictions[1].details == {
            "matched_count": 1,
            "job_skill_count": 2,
            "matched": ["Go"],
            "missing": ["Rust"],
            "threshold_percents": [20, 40, 60, 80],
        }
        assert all(type(p.fit_score) is int for p in predictions)


def test_keyword_baseline_rerun_adds_nothing_unless_an_input_changes(engine: Engine) -> None:
    resume = _baseline_setup(engine)
    with session_scope(engine) as session:
        run_keyword_baseline(session, resume, KeywordBaselineConfig(), "rubric_v1", "run-a")
    with session_scope(engine) as session:
        again = run_keyword_baseline(session, resume, KeywordBaselineConfig(), "rubric_v1", "run-b")
    assert (again.jobs_scored, again.jobs_unchanged) == (0, 2)
    with session_scope(engine) as session:
        changed = run_keyword_baseline(
            session,
            resume,
            KeywordBaselineConfig(threshold_percents=(10, 30, 50, 70)),
            "rubric_v1",
            "run-c",
        )
    assert changed.jobs_scored == 2
    with session_scope(engine) as session:
        assert session.scalar(select(func.count()).select_from(FitPrediction)) == 4


def test_keyword_baseline_needs_an_existing_rubric_file(engine: Engine) -> None:
    resume = _baseline_setup(engine)
    with pytest.raises(ValueError, match="no rubric file"), session_scope(engine) as session:
        run_keyword_baseline(session, resume, KeywordBaselineConfig(), "rubric_v99", "run-a")


def test_score_baseline_command_hides_scores_for_unlabeled_jobs(
    database: DatabaseConfig, engine: Engine, tmp_path: Path
) -> None:
    from typer.testing import CliRunner

    from jobsearch.cli import app

    _baseline_setup(engine)
    with session_scope(engine) as session:
        session.add(_label(2, 4, True, 1))
    config_file = tmp_path / "config.yaml"
    config_file.write_text(
        f"database:\n  path: {database.path}\nlogging:\n  file: {tmp_path / 'log.jsonl'}\n"
    )
    result = CliRunner().invoke(
        app, ["score", "baseline", "--config", str(config_file)], terminal_width=200
    )
    assert result.exit_code == 0, result.output
    output = " ".join(result.output.split())
    assert "2 jobs scored" in output
    assert "Scores for 1 jobs you haven't labeled are hidden" in output
    table = output.split("Keyword baseline on jobs you've labeled")[1]
    assert "1/2" in table
    assert "2/2" not in table


def test_upgrade_to_0008_adds_prediction_details_and_downgrade_removes_it(
    database: DatabaseConfig,
) -> None:
    from alembic import command
    from alembic.config import Config

    upgrade_database(database)
    config = Config("alembic.ini")
    config.attributes["database"] = database
    command.downgrade(config, "0007")
    engine = make_engine(database)
    with engine.connect() as connection:
        columns = [row[1] for row in connection.execute(text("PRAGMA table_info(fit_predictions)"))]
    engine.dispose()
    assert "details" not in columns
    assert "resume_version" in columns
