"""Resume text extraction. Every PDF here is synthetic."""

import io
from pathlib import Path

import pypdf
import pytest

from jobsearch.services.profiles import ResumeParseError, normalize_resume_text, parse_resume
from tests.unit.pdf_fixtures import make_pdf

RESUME = [
    ["JANE DOE", "SKILLS", "Languages:   Python,  Go (1.22)"],
    ["EXPERIENCE", "Example Corp  -  Remote      Jan 2020 - Present"],
]


def write(tmp_path: Path, data: bytes, name: str = "resume.pdf") -> Path:
    path = tmp_path / name
    path.write_bytes(data)
    return path


def test_extracts_each_line_in_order_with_single_spaces(tmp_path: Path) -> None:
    parsed = parse_resume(write(tmp_path, make_pdf(RESUME)))
    assert parsed.text.splitlines() == [
        "JANE DOE",
        "SKILLS",
        "Languages: Python, Go (1.22)",
        "",
        "EXPERIENCE",
        "Example Corp - Remote Jan 2020 - Present",
    ]
    assert parsed.page_count == 2
    assert parsed.source_filename == "resume.pdf"
    assert parsed.parser.startswith("pypdf ")


def test_same_text_has_the_same_hash_even_from_a_different_file(tmp_path: Path) -> None:
    first = parse_resume(write(tmp_path, make_pdf(RESUME), "a.pdf"))
    renamed = parse_resume(write(tmp_path, make_pdf(RESUME), "b.pdf"))
    changed = parse_resume(write(tmp_path, make_pdf([["JANE DOE", "SKILLS", "Rust"]]), "c.pdf"))
    assert first.text_hash == renamed.text_hash
    assert first.text_hash != changed.text_hash


def test_normalizes_ligatures_spacing_and_blank_lines() -> None:
    ligature_fi, no_break_space = chr(0xFB01), chr(0xA0)
    raw = f"\n\n  {ligature_fi}le   systems{no_break_space}and\tqueues  \n\n\n\nNext   \n\n"
    assert normalize_resume_text(raw) == "file systems and queues\n\nNext"


def test_rejects_a_pdf_without_text(tmp_path: Path) -> None:
    with pytest.raises(ResumeParseError, match="no text found"):
        parse_resume(write(tmp_path, make_pdf([[]])))


@pytest.mark.parametrize("data", [b"", b"not a pdf at all"])
def test_rejects_a_file_that_is_not_a_pdf(tmp_path: Path, data: bytes) -> None:
    with pytest.raises(ResumeParseError, match="not a readable PDF"):
        parse_resume(write(tmp_path, data))


def test_rejects_other_file_types(tmp_path: Path) -> None:
    with pytest.raises(ResumeParseError, match="only PDF"):
        parse_resume(write(tmp_path, b"Jane Doe", "resume.docx"))


def test_rejects_a_password_protected_pdf(tmp_path: Path) -> None:
    writer = pypdf.PdfWriter(clone_from=pypdf.PdfReader(io.BytesIO(make_pdf(RESUME))))
    writer.encrypt("secret", algorithm="RC4-128")
    buffer = io.BytesIO()
    writer.write(buffer)
    with pytest.raises(ResumeParseError, match="password-protected"):
        parse_resume(write(tmp_path, buffer.getvalue()))
