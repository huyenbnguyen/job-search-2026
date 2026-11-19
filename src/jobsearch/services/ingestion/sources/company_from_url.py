"""Company hints derived from a job URL, for jobs whose details couldn't be fetched.

The result is a slug, such as `exampleco` or `acme-robotics`, not a verified display
name. Records store it with `CompanySource.URL` so it's never mistaken for one, and
any better source, including the user, replaces it.
"""

import re
from collections.abc import Callable
from urllib.parse import unquote

import httpx

# Multi-tenant hiring systems name the company in a subdomain or path segment.
_TENANT_RULES: tuple[tuple[re.Pattern[str], Callable[[httpx.URL], str | None]], ...] = (
    (re.compile(r"^([\w-]+)\.wd\d+\.myworkdayjobs\.com$"), lambda url: url.host.split(".")[0]),
    (re.compile(r"^wd\d+\.myworkdaysite\.com$"), lambda url: _path_after(url, "recruiting")),
    (
        re.compile(r"^([\w-]+)\.fa(?:\.[\w-]+)*\.oraclecloud\.com$"),
        lambda url: url.host.split(".")[0],
    ),
    (re.compile(r"^([\w-]+)\.avature\.net$"), lambda url: url.host.split(".")[0]),
    (re.compile(r"^([\w-]+)\.hrmdirect\.com$"), lambda url: url.host.split(".")[0]),
    (re.compile(r"^jobs\.smartrecruiters\.com$"), lambda url: _path_segment(url, 0)),
    (re.compile(r"^ats\.rippling\.com$"), lambda url: _strip_board_suffix(_path_segment(url, 0))),
    (re.compile(r"^app\.careerpuck\.com$"), lambda url: _path_after(url, "job-board")),
    (re.compile(r"^(?:job-)?boards(?:\.eu)?\.greenhouse\.io$"), lambda url: _path_segment(url, 0)),
    (re.compile(r"^jobs(?:\.eu)?\.lever\.co$"), lambda url: _path_segment(url, 0)),
    (re.compile(r"^jobs\.ashbyhq\.com$"), lambda url: _path_segment(url, 0)),
    # LinkedIn is never fetched, but its long job URLs name the company:
    # /jobs/view/<title>-at-<company>-<id>/. Numeric-only URLs give nothing.
    (re.compile(r"^(?:[\w-]+\.)?linkedin\.com$"), lambda url: _linkedin_company(url)),
)  # fmt: skip

# Career-site domains often wrap the company name: careersatdoordash.com, metacareers.com.
_DOMAIN_AFFIXES = re.compile(r"^(?:careersat|careers|joinus|join|workat)|(?:careers|jobs)$")
# A leading locale path segment, such as /en-AU/ or /fr/.
_LOCALE = re.compile(r"^[a-z]{2}(?:[-_][a-z]{2})?$", re.I)

# Job sites whose domain says nothing about the hiring company.
_AGGREGATOR_DOMAINS = frozenset({"linkedin", "indeed", "glassdoor", "ziprecruiter", "wellfound"})

# Two-label public suffixes, for domains like example.co.uk.
_TWO_LABEL_SUFFIXES = frozenset(
    {"co.uk", "com.au", "co.jp", "co.in", "com.br", "co.nz", "com.sg", "co.kr", "com.mx"}
)


def company_from_url(url: httpx.URL) -> str | None:
    """A lowercase company slug from the URL's ATS tenant or domain, or None."""
    host = url.host.lower()
    for pattern, extract in _TENANT_RULES:
        if pattern.match(host):
            return _clean(extract(url))
    label = _registrable_label(host)
    if label is None or label in _AGGREGATOR_DOMAINS:
        return None
    return _clean(_DOMAIN_AFFIXES.sub("", label) or label)


def _registrable_label(host: str) -> str | None:
    labels = [label for label in host.split(".") if label]
    if len(labels) < 2:
        return None
    suffix_length = 2 if ".".join(labels[-2:]) in _TWO_LABEL_SUFFIXES else 1
    if len(labels) <= suffix_length:
        return None
    return labels[-(suffix_length + 1)]


_LINKEDIN_SLUG = re.compile(r"^/jobs/view/[\w-]*?-at-(?P<company>(?:(?!-at-)[\w-])+?)-\d+/?$")


def _linkedin_company(url: httpx.URL) -> str | None:
    match = _LINKEDIN_SLUG.match(url.path)
    return match["company"] if match else None


def _path_parts(url: httpx.URL) -> list[str]:
    parts = [unquote(part) for part in url.path.split("/") if part]
    return parts[1:] if parts and _LOCALE.match(parts[0]) else parts


def _path_segment(url: httpx.URL, index: int) -> str | None:
    parts = _path_parts(url)
    return parts[index] if len(parts) > index else None


def _strip_board_suffix(slug: str | None) -> str | None:
    return slug.removesuffix("-job-board") if slug else None


def _path_after(url: httpx.URL, marker: str) -> str | None:
    parts = _path_parts(url)
    if marker in parts and parts.index(marker) + 1 < len(parts):
        return parts[parts.index(marker) + 1]
    return None


def _clean(value: str | None) -> str | None:
    if not value:
        return None
    cleaned = value.strip().lower()
    return cleaned if re.fullmatch(r"[\w .&-]{1,100}", cleaned) else None
