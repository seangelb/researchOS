"""Shared helpers for Variant gaming collectors (hashing, paths, money parsing)."""

from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd
import requests

USER_AGENT = "researchOS-variant-gaming/1.0 (+official regulator downloads)"


def project_root() -> Path:
    """Return repo root whether the cwd is the repo or notebooks/."""
    here = Path.cwd().resolve()
    for candidate in [here, *here.parents]:
        if (candidate / "config" / "state_gaming_source_inventory.csv").exists():
            return candidate
    return here


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_text(text: str, encoding: str = "utf-8") -> str:
    return sha256_bytes(text.encode(encoding))


def retrieval_date_dir(root: Path, state_code: str, retrieved_at: datetime | None = None) -> Path:
    """data/raw/<STATE>/<YYYY-MM-DD>/ — never overwrite contents."""
    when = retrieved_at or utc_now()
    path = root / "data" / "raw" / state_code.upper() / when.date().isoformat()
    path.mkdir(parents=True, exist_ok=True)
    return path


def immutable_raw_path(directory: Path, content_sha256: str, original_filename: str) -> Path:
    """Filename: <sha256>_<safe_original_filename>."""
    safe = re.sub(r"[^\w.\-]+", "_", original_filename).strip("_")
    return directory / f"{content_sha256}_{safe}"


def save_raw_bytes(
    root: Path,
    state_code: str,
    content: bytes,
    original_filename: str,
    retrieved_at: datetime | None = None,
) -> Path:
    """
    Save raw bytes under data/raw/<STATE>/<date>/<sha256>_<name>.
    If the same hash already exists anywhere under that date folder, reuse it.
    Never overwrite a different file.
    """
    digest = sha256_bytes(content)
    directory = retrieval_date_dir(root, state_code, retrieved_at)
    path = immutable_raw_path(directory, digest, original_filename)
    if path.exists():
        existing = path.read_bytes()
        if existing != content:
            raise RuntimeError(f"Hash collision with different bytes: {path}")
        return path
    # Reuse same bytes under another filename that day (verify content — sidecars
    # may reuse the content hash in their name without being the payload).
    for existing_path in directory.glob(f"{digest}_*"):
        if sha256_bytes(existing_path.read_bytes()) == digest:
            return existing_path
    path.write_bytes(content)
    return path


def save_raw_text(
    root: Path,
    state_code: str,
    text: str,
    original_filename: str,
    retrieved_at: datetime | None = None,
    encoding: str = "utf-8",
) -> Path:
    return save_raw_bytes(
        root,
        state_code,
        text.encode(encoding),
        original_filename,
        retrieved_at=retrieved_at,
    )


def http_get_unchecked(url: str, session: requests.Session | None = None, **kwargs: Any) -> requests.Response:
    """GET with TLS verification on. Does not raise for HTTP error statuses."""
    sess = session or requests.Session()
    headers = {"User-Agent": USER_AGENT}
    headers.update(kwargs.pop("headers", {}) or {})
    timeout = kwargs.pop("timeout", 120)
    return sess.get(url, headers=headers, timeout=timeout, verify=True, **kwargs)


def http_get(url: str, session: requests.Session | None = None, **kwargs: Any) -> requests.Response:
    """GET with TLS verification on and a stable User-Agent."""
    response = http_get_unchecked(url, session=session, **kwargs)
    response.raise_for_status()
    return response


def http_block_reason(response: requests.Response) -> str | None:
    """Return a coverage reason for HTTP 401/403 or an interstitial challenge page."""
    status = response.status_code
    if status in {401, 403}:
        return f"HTTP {status} blocked from {response.url}"
    headers = {k.casefold(): v for k, v in (response.headers or {}).items()}
    if headers.get("cf-mitigated") == "challenge":
        return f"Captcha/challenge page from {response.url}"
    snippet = (response.text or "")[:1500].casefold()
    if "checking your browser before accessing" in snippet:
        return f"Captcha/challenge page from {response.url}"
    if "verify you are human" in snippet and "cloudflare" in snippet:
        return f"Captcha/challenge page from {response.url}"
    return None


def http_post(url: str, data: dict | None = None, session: requests.Session | None = None, **kwargs: Any) -> requests.Response:
    sess = session or requests.Session()
    headers = {"User-Agent": USER_AGENT}
    headers.update(kwargs.pop("headers", {}) or {})
    timeout = kwargs.pop("timeout", 120)
    response = sess.post(url, data=data, headers=headers, timeout=timeout, verify=True, **kwargs)
    response.raise_for_status()
    return response


def parse_money(value: Any) -> float | None:
    """Parse currency-like strings to float. Preserve negatives. Return None for blanks."""
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    text = str(value).strip()
    if text == "" or text.lower() in {"nan", "none", "n/a", "-", "—"}:
        return None
    negative = False
    if text.startswith("(") and text.endswith(")"):
        negative = True
        text = text[1:-1]
    text = text.replace("$", "").replace(",", "").replace("\xa0", "").strip()
    text = re.sub(r"(?<=\d)\s+(?=\d)", "", text)
    if text.startswith("-"):
        negative = True
        text = text[1:].strip()
    if text == "":
        return None
    try:
        number = float(text)
    except ValueError:
        return None
    return -number if negative else number


def month_period(year: int, month: int) -> tuple[str, str]:
    start = pd.Timestamp(year=year, month=month, day=1)
    end = start + pd.offsets.MonthEnd(0)
    return start.date().isoformat(), end.date().isoformat()


MONTH_NAMES = [
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
]


def month_name_to_num(name: str) -> int:
    return MONTH_NAMES.index(name) + 1
