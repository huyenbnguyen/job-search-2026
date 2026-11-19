"""Load a resume: extract its text and store it as a versioned profile.

Only text-based PDFs are supported. A scanned PDF has no text to extract, and OCR isn't
attempted. The text is what scoring sees and what quoted evidence is checked against,
so it's normalized the same way every time: NFKC (which also splits ligatures such as
"fi"), one space between words, and at most one blank line in a row.

Each distinct text is stored once and never overwritten. Resume text is personal data:
never log it, only its hash or row ID.
"""

import hashlib
import io
import re
import unicodedata
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import pypdf
from pypdf.errors import PdfReadError
from sqlalchemy import select
from sqlalchemy.orm import Session

from jobsearch.models.tables import ResumeVersion

PARSER = f"pypdf {pypdf.__version__} layout"

_SPACES = re.compile(r"\s+")


class ResumeParseError(ValueError):
    pass


@dataclass(frozen=True)
class ParsedResume:
    text: str
    text_hash: str
    file_hash: str
    page_count: int
    source_filename: str
    parser: str = PARSER


def normalize_resume_text(raw: str) -> str:
    lines = [
        _SPACES.sub(" ", line).strip() for line in unicodedata.normalize("NFKC", raw).splitlines()
    ]
    kept: list[str] = []
    for line in lines:
        if line or (kept and kept[-1]):
            kept.append(line)
    return "\n".join(kept).strip()


def parse_resume(path: Path) -> ParsedResume:
    """Extract and normalize the text of a text-based PDF resume."""
    if path.suffix.lower() != ".pdf":
        raise ResumeParseError(f"{path.name}: only PDF resumes are supported")
    data = path.read_bytes()
    try:
        reader = pypdf.PdfReader(io.BytesIO(data))
        if reader.is_encrypted:
            raise ResumeParseError(f"{path.name}: the PDF is password-protected")
        # Layout mode keeps each line together. The default mode splits Google Docs
        # exports into one word per line.
        pages = [page.extract_text(extraction_mode="layout") for page in reader.pages]
    except PdfReadError as exc:
        raise ResumeParseError(f"{path.name}: not a readable PDF ({exc})") from exc
    text = normalize_resume_text("\n\n".join(pages))
    if not text:
        raise ResumeParseError(
            f"{path.name}: no text found. It may be a scanned image; export a text-based PDF."
        )
    return ParsedResume(
        text=text,
        text_hash=hashlib.sha256(text.encode()).hexdigest(),
        file_hash=hashlib.sha256(data).hexdigest(),
        page_count=len(pages),
        source_filename=path.name,
    )


def save_resume_version(session: Session, parsed: ParsedResume) -> tuple[ResumeVersion, bool]:
    """Store the resume text unless it's already stored. Returns the row and whether it's new."""
    existing = session.scalar(
        select(ResumeVersion).where(ResumeVersion.text_hash == parsed.text_hash)
    )
    if existing is not None:
        return existing, False
    version = ResumeVersion(
        text_hash=parsed.text_hash,
        text=parsed.text,
        source_filename=parsed.source_filename,
        file_hash=parsed.file_hash,
        page_count=parsed.page_count,
        parser=parsed.parser,
        created_at=datetime.now(UTC),
    )
    session.add(version)
    session.flush()
    return version, True
