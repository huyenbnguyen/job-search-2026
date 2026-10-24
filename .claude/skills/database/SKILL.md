---
name: database
description: Rules for the local SQLite database — SQLAlchemy access, Alembic migrations, connection settings, idempotent writes, versioning, and backups. Use before changing the schema, writing queries, or adding imports.
---

# Database

## Setup
- SQLAlchemy and Alembic remain proposed until the relevant phase-start decision. Ask the user before implementing database access or migrations.
- SQLite, one local file, accessed through SQLAlchemy.
- The database file lives outside the repository and is never committed. Its path comes from config.
- Open every connection with WAL mode and a busy timeout, so a scheduled run and a CLI session can overlap safely.
- Keep access in SQLAlchemy and avoid SQLite-only SQL unless it's needed, so a later move to PostgreSQL stays mostly a connection-string change.

## Migrations
- Every schema change is a checked-in Alembic migration. Never alter the schema by hand or at app startup.
- Migrations must run cleanly on an existing database with real data; test upgrade paths.

## Idempotency
- Imports and runs must be safe to rerun. Use unique constraints and upserts (`INSERT ... ON CONFLICT`) so reruns don't create duplicates.
- Deduplicate jobs on a stable key, such as normalized source URL.
- Wrap each logical unit of work in a transaction.

## Versioning and provenance
- Version job descriptions and resume profiles; never overwrite an earlier version.
- Every job record keeps its source URL and retrieval timestamp.
- Predictions reference the exact job version, resume version, model ID, prompt version, and rubric version they used.
- Human labels and model scores live in separate tables.
- Feedback events are append-only.

## Backups
- Back up with SQLite's online backup API, which is safe while the app may be running. Don't copy the file directly during a write.
- Backup location and frequency are TBD; ask the user before choosing.
- Document and test restore in the README.

## Privacy
- Store parsed resume text and metadata in the database; original files stay in a local folder.
- Never log row contents that include resume text. Log IDs.
