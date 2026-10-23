import argparse
import collections
import json
import logging
import os
import re
import sys
import tempfile
import time
from typing import Any

import requests

# ---------------------------------------------------------------------------
# Configuration & Constants
# ---------------------------------------------------------------------------

DEFAULT_USERNAME = "ParijatSoftwares"
DEFAULT_OUTPUT_FILE = "anime.json"
MAL_LOAD_URL = "https://myanimelist.net/animelist/{username}/load.json"

DEFAULT_REQUEST_TIMEOUT = 20.0
DEFAULT_REQUEST_DELAY = 1.0
DEFAULT_MAX_RETRIES = 3
DEFAULT_BACKOFF_FACTOR = 2.0

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

# Status mapping for MyAnimeList
# 1: Watching, 2: Completed, 3: On Hold, 4: Dropped, 6: Plan to Watch
STATUS_MAP = {
    1: "Watching",
    2: "Completed",
    3: "On Hold",
    4: "Dropped",
    6: "Plan to Watch",
}
VALID_STATUSES = set(STATUS_MAP.keys())
RELEVANT_LIST_STATUSES = [1, 2, 3, 4, 6]

# Logging configuration
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("anime_generator")


# ---------------------------------------------------------------------------
# Custom Exceptions
# ---------------------------------------------------------------------------

class MALGeneratorError(Exception):
    """Base exception for Anime Wall generator."""
    pass


class MALFetchError(MALGeneratorError):
    """Raised when fetching pages from MAL fails unrecoverably."""
    pass


class PaginationLoopError(MALGeneratorError):
    """Raised when a pagination loop or repeated page is detected."""
    pass


class ValidationError(MALGeneratorError):
    """Raised when downloaded data fails integrity validation."""
    pass


# ---------------------------------------------------------------------------
# Image & Metadata Processing
# ---------------------------------------------------------------------------

def process_image_url(raw_image: Any) -> tuple[str, bool]:
    """
    Transforms a MyAnimeList image URL to a high-resolution poster URL.
    Handles missing, empty, malformed, or extensionless URLs gracefully.

    Returns:
        tuple[str, bool]: (processed_url, is_valid_poster)
    """
    if not raw_image or not isinstance(raw_image, str):
        return ("", False)

    cleaned = raw_image.strip()
    if not cleaned:
        return ("", False)

    # Strip any thumbnail resizer path like /r/192x272 or /r/96x136
    cleaned = re.sub(r"/r/\d+x\d+", "", cleaned)

    # Strip query parameters (?s=...)
    cleaned = cleaned.split("?")[0].strip()

    # Detect MAL placeholder images (e.g., qm_50.gif)
    is_placeholder = "qm_50" in cleaned or "/qm_" in cleaned

    parts = cleaned.rsplit("/", 1)
    if len(parts) == 2:
        dir_part, filename = parts
        if "." in filename and not is_placeholder:
            base, ext = filename.rsplit(".", 1)
            # Avoid appending another 'l' if it already ends in 'l'
            if not base.endswith("l"):
                base = f"{base}l"
            cleaned = f"{dir_part}/{base}.{ext}"
    else:
        if "." in cleaned and not is_placeholder:
            base, ext = cleaned.rsplit(".", 1)
            if not base.endswith("l"):
                base = f"{base}l"
            cleaned = f"{base}.{ext}"

    is_valid = (
        (cleaned.startswith("http://") or cleaned.startswith("https://"))
        and not is_placeholder
    )
    return (cleaned, is_valid)


def normalize_entry(raw_anime: dict[str, Any]) -> tuple[dict[str, Any] | None, str | None, dict[str, bool]]:
    """
    Validates required fields and normalizes an anime entry into the frontend schema.

    Returns:
        tuple: (normalized_dict or None, error_reason or None, metadata_flags)
    """
    metadata_flags = {
        "missing_poster": False,
        "unrated_score": False,
    }

    # 1. Validate required field: anime_id
    raw_id = raw_anime.get("anime_id")
    if raw_id is None:
        raw_id = raw_anime.get("id")

    try:
        anime_id = int(raw_id)  # type: ignore[arg-type]
        if anime_id <= 0:
            return (None, f"Non-positive anime ID: {raw_id!r}", metadata_flags)
    except (ValueError, TypeError):
        return (None, f"Malformed or missing anime ID: {raw_id!r}", metadata_flags)

    # 2. Validate required field: anime_title
    raw_title = raw_anime.get("anime_title")
    if raw_title is None:
        raw_title = raw_anime.get("title")

    if raw_title is None or not isinstance(raw_title, str) or not raw_title.strip():
        return (None, f"Missing or empty anime title for anime ID {anime_id}", metadata_flags)
    title = raw_title.strip()

    # 3. Validate and normalize status
    raw_status = raw_anime.get("status")
    status_val = None
    if isinstance(raw_status, int):
        status_val = raw_status
    elif isinstance(raw_status, str):
        status_str = raw_status.strip().lower()
        alias_map = {
            "1": 1, "watching": 1, "currently_watching": 1, "currently watching": 1,
            "2": 2, "finished": 2, "completed": 2, "complete": 2,
            "3": 3, "on_hold": 3, "on hold": 3, "paused": 3,
            "4": 4, "dropped": 4,
            "6": 6, "plan_to_watch": 6, "plan to watch": 6, "planned": 6, "ptw": 6,
        }
        status_val = alias_map.get(status_str)
        if status_val is None:
            try:
                status_val = int(status_str)
            except ValueError:
                pass

    if status_val is None or status_val not in VALID_STATUSES:
        return (None, f"Invalid or unrecognized status {raw_status!r} for anime ID {anime_id} ('{title}')", metadata_flags)

    # 4. Canonical anime URL
    url = f"https://myanimelist.net/anime/{anime_id}"

    # 5. Optional metadata: image poster URL
    raw_image = raw_anime.get("anime_image_path")
    if raw_image is None:
        raw_image = raw_anime.get("image")
    image_url, has_valid_poster = process_image_url(raw_image)
    if not has_valid_poster:
        metadata_flags["missing_poster"] = True

    # 6. Optional metadata: score
    raw_score = raw_anime.get("score")
    score = 0
    if raw_score is not None:
        try:
            score_num = int(round(float(raw_score)))
            if 1 <= score_num <= 10:
                score = score_num
            else:
                score = 0
        except (ValueError, TypeError):
            score = 0

    if score == 0:
        metadata_flags["unrated_score"] = True

    normalized = {
        "id": anime_id,
        "title": title,
        "image": image_url,
        "url": url,
        "score": score,
        "status": status_val,
    }
    return (normalized, None, metadata_flags)


# ---------------------------------------------------------------------------
# API Fetching & Pagination
# ---------------------------------------------------------------------------

def fetch_page(
    session: requests.Session,
    username: str,
    status: int,
    offset: int,
    timeout: float = DEFAULT_REQUEST_TIMEOUT,
    max_retries: int = DEFAULT_MAX_RETRIES,
    backoff_factor: float = DEFAULT_BACKOFF_FACTOR,
) -> list[dict[str, Any]]:
    """
    Fetches a single page of anime entries from MyAnimeList with retries.

    Raises:
        MALFetchError: If the request fails after exhausting retries or returns invalid data.
    """
    url = MAL_LOAD_URL.format(username=username)
    params = {"status": status, "offset": offset}
    headers = {"User-Agent": USER_AGENT}

    for attempt in range(1, max_retries + 1):
        try:
            logger.debug(f"Fetching status={status}, offset={offset} (attempt {attempt}/{max_retries})...")
            response = session.get(url, params=params, headers=headers, timeout=timeout)

            # Check for rate limiting
            if response.status_code == 429:
                retry_after_hdr = response.headers.get("Retry-After")
                if retry_after_hdr and retry_after_hdr.isdigit():
                    wait_time = float(retry_after_hdr) + 1.0
                else:
                    wait_time = backoff_factor ** attempt

                logger.warning(
                    f"HTTP 429 (Rate Limited) at offset {offset}. Retrying in {wait_time:.1f}s (attempt {attempt}/{max_retries})..."
                )
                if attempt < max_retries:
                    time.sleep(wait_time)
                    continue
                raise MALFetchError(f"HTTP 429 Rate limited at status={status}, offset={offset} after {max_retries} attempts.")

            # Check for transient server errors (500, 502, 503, 504)
            if response.status_code in {500, 502, 503, 504}:
                wait_time = backoff_factor ** attempt
                logger.warning(
                    f"HTTP {response.status_code} Server Error at offset {offset}. Retrying in {wait_time:.1f}s (attempt {attempt}/{max_retries})..."
                )
                if attempt < max_retries:
                    time.sleep(wait_time)
                    continue
                raise MALFetchError(
                    f"HTTP {response.status_code} Server Error at status={status}, offset={offset} after {max_retries} attempts."
                )

            # Other HTTP errors
            response.raise_for_status()

            # Parse JSON
            try:
                data = response.json()
            except (json.JSONDecodeError, ValueError) as json_err:
                wait_time = backoff_factor ** attempt
                logger.warning(
                    f"Invalid JSON received at offset {offset} ({json_err}). Retrying in {wait_time:.1f}s (attempt {attempt}/{max_retries})..."
                )
                if attempt < max_retries:
                    time.sleep(wait_time)
                    continue
                raise MALFetchError(
                    f"Invalid JSON received at status={status}, offset={offset} after {max_retries} attempts."
                )

            # Ensure response is a list
            if not isinstance(data, list):
                wait_time = backoff_factor ** attempt
                logger.warning(
                    f"Unexpected response format (expected list, got {type(data).__name__}) at offset {offset}. Retrying in {wait_time:.1f}s..."
                )
                if attempt < max_retries:
                    time.sleep(wait_time)
                    continue
                raise MALFetchError(
                    f"API returned non-list response at status={status}, offset={offset}: {str(data)[:200]}"
                )

            return data

        except requests.RequestException as req_err:
            wait_time = backoff_factor ** attempt
            logger.warning(
                f"Network error at offset {offset} ({req_err}). Retrying in {wait_time:.1f}s (attempt {attempt}/{max_retries})..."
            )
            if attempt < max_retries:
                time.sleep(wait_time)
                continue
            raise MALFetchError(
                f"Network request failed for status={status}, offset={offset} after {max_retries} attempts: {req_err}"
            )

    raise MALFetchError(f"Failed to fetch status={status}, offset={offset} after {max_retries} attempts.")


def fetch_status_pages(
    session: requests.Session,
    username: str,
    status: int,
    delay: float = DEFAULT_REQUEST_DELAY,
    timeout: float = DEFAULT_REQUEST_TIMEOUT,
    max_retries: int = DEFAULT_MAX_RETRIES,
    backoff_factor: float = DEFAULT_BACKOFF_FACTOR,
) -> tuple[list[dict[str, Any]], list[int]]:
    """
    Paginates through all pages for a given status until the API returns an empty list.

    Returns:
        tuple: (all_entries, visited_offsets)
    """
    offset = 0
    visited_offsets: list[int] = []
    visited_offset_set: set[int] = set()
    all_entries: list[dict[str, Any]] = []
    seen_ids_in_pagination: set[int] = set()

    max_pages = 500  # Safety threshold to prevent infinite loops (150,000 entries)
    page_count = 0

    while True:
        page_count += 1
        if page_count > max_pages:
            raise PaginationLoopError(f"Exceeded maximum page count limit ({max_pages}) for status {status}.")

        if offset in visited_offset_set:
            raise PaginationLoopError(
                f"Pagination loop detected: offset {offset} has already been requested for status {status}."
            )

        visited_offsets.append(offset)
        visited_offset_set.add(offset)

        logger.info(f"Requesting status={status}, offset={offset}...")
        page = fetch_page(
            session=session,
            username=username,
            status=status,
            offset=offset,
            timeout=timeout,
            max_retries=max_retries,
            backoff_factor=backoff_factor,
        )

        entries_on_page = len(page)
        logger.info(f"Received {entries_on_page} entries at offset={offset}.")

        # If empty page is returned, pagination is complete
        if entries_on_page == 0:
            break

        # Detect pagination loops where the API returns identical entries
        page_ids: list[int] = []
        for item in page:
            if isinstance(item, dict):
                raw_id = item.get("anime_id") or item.get("id")
                try:
                    if raw_id is not None:
                        page_ids.append(int(raw_id))
                except (ValueError, TypeError):
                    pass

        new_ids = [aid for aid in page_ids if aid not in seen_ids_in_pagination]
        if page_ids and len(new_ids) == 0:
            raise PaginationLoopError(
                f"Pagination loop detected at offset {offset}: all {entries_on_page} entries in page "
                f"have already been encountered in previous pages."
            )

        seen_ids_in_pagination.update(new_ids)
        all_entries.extend(page)

        # Advance offset by the ACTUAL number of entries returned
        offset += entries_on_page

        if delay > 0:
            time.sleep(delay)

    return all_entries, visited_offsets


def fetch_all_mal_entries(
    username: str = DEFAULT_USERNAME,
    use_status_7: bool = True,
    delay: float = DEFAULT_REQUEST_DELAY,
    timeout: float = DEFAULT_REQUEST_TIMEOUT,
    max_retries: int = DEFAULT_MAX_RETRIES,
    backoff_factor: float = DEFAULT_BACKOFF_FACTOR,
    session: requests.Session | None = None,
) -> tuple[list[dict[str, Any]], dict[str, list[int]], str]:
    """
    Fetches the full anime list for username.
    Tries status=7 first if use_status_7 is True.
    If status=7 fails, falls back to paginating each status in [1, 2, 3, 4, 6].

    Returns:
        tuple: (raw_entries, visited_offsets_dict, strategy_used)
    """
    managed_session = session or requests.Session()

    try:
        if use_status_7:
            try:
                logger.info(f"Retrieving anime list for '{username}' using status=7 (all entries)...")
                entries, offsets = fetch_status_pages(
                    session=managed_session,
                    username=username,
                    status=7,
                    delay=delay,
                    timeout=timeout,
                    max_retries=max_retries,
                    backoff_factor=backoff_factor,
                )
                return entries, {"status_7": offsets}, "status=7 (complete list)"
            except Exception as e:
                logger.warning(
                    f"Retrieval using status=7 failed: {e}. Falling back to per-status pagination..."
                )

        logger.info(f"Retrieving anime list for '{username}' by paginating individual statuses {RELEVANT_LIST_STATUSES}...")
        all_entries: list[dict[str, Any]] = []
        offsets_by_status: dict[str, list[int]] = {}

        for status in RELEVANT_LIST_STATUSES:
            status_name = STATUS_MAP.get(status, f"status {status}")
            logger.info(f"Paginating status={status} ({status_name})...")
            entries, offsets = fetch_status_pages(
                session=managed_session,
                username=username,
                status=status,
                delay=delay,
                timeout=timeout,
                max_retries=max_retries,
                backoff_factor=backoff_factor,
            )
            all_entries.extend(entries)
            offsets_by_status[f"status_{status}"] = offsets

        return all_entries, offsets_by_status, "per-status pagination"

    finally:
        if session is None:
            managed_session.close()


# ---------------------------------------------------------------------------
# Data Processing & Validation
# ---------------------------------------------------------------------------

def process_raw_entries(
    raw_entries: list[dict[str, Any]]
) -> tuple[list[dict[str, Any]], int, dict[str, int], list[tuple[Any, str]]]:
    """
    Normalizes raw API entries, deduplicates by ID, and verifies data integrity.

    Returns:
        tuple: (unique_entries, duplicate_count, metadata_stats, malformed_entries)
    """
    processed_anime: list[dict[str, Any]] = []
    seen_ids: set[int] = set()
    duplicate_count = 0
    malformed_entries: list[tuple[Any, str]] = []

    metadata_stats = {
        "missing_poster": 0,
        "unrated_score": 0,
        "entries_with_missing_optional": 0,
    }

    for raw in raw_entries:
        if not isinstance(raw, dict):
            malformed_entries.append(("unknown", f"Raw entry is not a dictionary: {type(raw).__name__}"))
            continue

        normalized, error_reason, flags = normalize_entry(raw)
        if error_reason or normalized is None:
            raw_id = raw.get("anime_id") or raw.get("id") or "unknown"
            malformed_entries.append((raw_id, error_reason or "Unknown error"))
            continue

        anime_id = normalized["id"]
        if anime_id in seen_ids:
            duplicate_count += 1
            logger.warning(
                f"Duplicate anime ID {anime_id} ('{normalized['title']}') detected; retaining initial entry."
            )
            continue

        seen_ids.add(anime_id)
        processed_anime.append(normalized)

        has_missing_opt = False
        if flags["missing_poster"]:
            metadata_stats["missing_poster"] += 1
            has_missing_opt = True
        if flags["unrated_score"]:
            metadata_stats["unrated_score"] += 1
            has_missing_opt = True
        if has_missing_opt:
            metadata_stats["entries_with_missing_optional"] += 1

    return processed_anime, duplicate_count, metadata_stats, malformed_entries


def validate_dataset(
    entries: list[dict[str, Any]],
    expected_unique_ids: set[int] | None = None,
) -> None:
    """
    Validates that the dataset conforms to required schema and completeness rules.

    Raises:
        ValidationError: If dataset is invalid or incomplete.
    """
    if not isinstance(entries, list):
        raise ValidationError(f"Dataset must be a list, got {type(entries).__name__}.")

    if len(entries) == 0:
        raise ValidationError("Dataset contains 0 entries. Refusing to publish an empty anime list.")

    required_keys = {"id", "title", "image", "url", "score", "status"}
    seen_ids: set[int] = set()

    for idx, entry in enumerate(entries):
        if not isinstance(entry, dict):
            raise ValidationError(f"Entry at index {idx} is not a dictionary.")

        missing_keys = required_keys - set(entry.keys())
        if missing_keys:
            raise ValidationError(f"Entry {entry.get('id', idx)} is missing required keys: {missing_keys}")

        # ID validation
        entry_id = entry["id"]
        if not isinstance(entry_id, int) or entry_id <= 0:
            raise ValidationError(f"Entry at index {idx} has invalid ID: {entry_id!r}")

        if entry_id in seen_ids:
            raise ValidationError(f"Duplicate anime ID {entry_id} found in dataset.")
        seen_ids.add(entry_id)

        # Title validation
        title = entry["title"]
        if not isinstance(title, str) or not title.strip():
            raise ValidationError(f"Entry ID {entry_id} has invalid or empty title: {title!r}")

        # URL validation
        url = entry["url"]
        if not isinstance(url, str) or not url.startswith("https://myanimelist.net/anime/"):
            raise ValidationError(f"Entry ID {entry_id} has invalid URL: {url!r}")

        # Image validation
        image = entry["image"]
        if not isinstance(image, str):
            raise ValidationError(f"Entry ID {entry_id} has non-string image: {image!r}")

        # Score validation
        score = entry["score"]
        if not isinstance(score, int) or not (0 <= score <= 10):
            raise ValidationError(f"Entry ID {entry_id} has invalid score: {score!r}")

        # Status validation
        status = entry["status"]
        if not isinstance(status, int) or status not in VALID_STATUSES:
            raise ValidationError(f"Entry ID {entry_id} has invalid status: {status!r}")

    if expected_unique_ids is not None:
        if seen_ids != expected_unique_ids:
            missing = expected_unique_ids - seen_ids
            extra = seen_ids - expected_unique_ids
            raise ValidationError(
                f"Dataset unique IDs do not match expected IDs. Missing: {len(missing)}, Unexpected extra: {len(extra)}."
            )


# ---------------------------------------------------------------------------
# Diagnostics & Summary
# ---------------------------------------------------------------------------

def load_existing_anime_json(filepath: str) -> list[dict[str, Any]] | None:
    """Safely loads the existing anime.json file if present."""
    if not os.path.exists(filepath):
        return None
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, list):
                return data
    except Exception as exc:
        logger.warning(f"Could not read existing '{filepath}' for comparison: {exc}")
    return None


def print_summary(
    raw_count: int,
    unique_count: int,
    visited_offsets: dict[str, list[int]],
    strategy: str,
    status_counts: dict[int, int],
    duplicate_count: int,
    metadata_stats: dict[str, int],
    existing_file: str,
    new_anime_list: list[dict[str, Any]],
) -> None:
    """Prints a detailed diagnostic and comparison summary."""
    existing_data = load_existing_anime_json(existing_file)

    print("\n" + "=" * 65)
    print("                     ANIMELIST GENERATION SUMMARY")
    print("=" * 65)
    print(f"* Retrieval strategy:                      {strategy}")
    print(f"* Total raw entries fetched across pages:  {raw_count}")
    print(f"* Total unique MyAnimeList IDs:            {unique_count}")
    print(f"* Unexpected duplicate entries detected:   {duplicate_count}")

    print("\n* Pages & Offsets:")
    total_pages = 0
    for key, offsets in visited_offsets.items():
        total_pages += len(offsets)
        print(f"  - {key}: {len(offsets)} pages (offsets: {offsets})")
    print(f"  - Total API pages requested: {total_pages}")

    print("\n* Entries by status:")
    for status_code in sorted(VALID_STATUSES):
        name = STATUS_MAP.get(status_code, "Unknown")
        count = status_counts.get(status_code, 0)
        print(f"  - [{status_code}] {name:15s}: {count:5d}")
    print(f"  - Total entries categorized : {sum(status_counts.values()):5d}")

    print("\n* Metadata health:")
    print(f"  - Missing or placeholder posters:         {metadata_stats.get('missing_poster', 0)}")
    print(f"  - Unrated entries (score=0):              {metadata_stats.get('unrated_score', 0)}")
    print(f"  - Entries with missing optional metadata: {metadata_stats.get('entries_with_missing_optional', 0)}")

    print("\n* Existing vs New comparison:")
    if existing_data is None:
        print(f"  - No existing '{existing_file}' found (initial generation).")
    else:
        existing_ids = {item["id"] for item in existing_data if isinstance(item, dict) and "id" in item}
        new_ids = {item["id"] for item in new_anime_list}

        added = new_ids - existing_ids
        removed = existing_ids - new_ids

        print(f"  - Existing file entry count:             {len(existing_data)}")
        print(f"  - Newly generated entry count:           {len(new_anime_list)}")
        print(f"  - Newly added entries (+):               {len(added)}")
        print(f"  - Removed entries (-):                   {len(removed)}")

        if added:
            sample_added = list(added)[:5]
            print(f"    Sample newly added IDs: {sample_added}{'...' if len(added) > 5 else ''}")
        if removed:
            sample_removed = list(removed)[:5]
            print(f"    Sample removed IDs: {sample_removed}{'...' if len(removed) > 5 else ''}")

    print("=" * 65 + "\n")


# ---------------------------------------------------------------------------
# Atomic File Publishing
# ---------------------------------------------------------------------------

def save_anime_json_atomically(
    anime_list: list[dict[str, Any]],
    output_file: str = DEFAULT_OUTPUT_FILE,
    expected_unique_ids: set[int] | None = None,
) -> None:
    """
    Validates dataset, writes to a temporary file in the target directory,
    verifies readability, and atomically replaces the output file.

    Preserves existing file on any failure.
    """
    # 1. In-memory validation
    validate_dataset(anime_list, expected_unique_ids=expected_unique_ids)

    target_path = os.path.abspath(output_file)
    target_dir = os.path.dirname(target_path) or "."
    os.makedirs(target_dir, exist_ok=True)

    # 2. Write to temp file in target directory
    temp_fd, temp_path = tempfile.mkstemp(prefix="anime_", suffix=".tmp", dir=target_dir)
    try:
        with os.fdopen(temp_fd, "w", encoding="utf-8") as f:
            json.dump(anime_list, f, ensure_ascii=False, indent=2)
            f.write("\n")

        # 3. Verify written file can be read and parsed back
        with open(temp_path, "r", encoding="utf-8") as f:
            written_data = json.load(f)

        if len(written_data) != len(anime_list):
            raise IOError("Written file verification failed: entry count mismatch.")

        # 4. Atomic replace
        os.replace(temp_path, target_path)
        logger.info(f"Successfully and atomically published {len(anime_list)} entries to '{output_file}'.")

    except Exception:
        # Clean up temp file on failure
        if os.path.exists(temp_path):
            try:
                os.remove(temp_path)
            except OSError:
                pass
        raise


# ---------------------------------------------------------------------------
# Main Orchestrator
# ---------------------------------------------------------------------------

def generate(
    username: str = DEFAULT_USERNAME,
    output_file: str = DEFAULT_OUTPUT_FILE,
    delay: float = DEFAULT_REQUEST_DELAY,
    timeout: float = DEFAULT_REQUEST_TIMEOUT,
    max_retries: int = DEFAULT_MAX_RETRIES,
    backoff_factor: float = DEFAULT_BACKOFF_FACTOR,
    session: requests.Session | None = None,
) -> list[dict[str, Any]]:
    """
    Executes the full pipeline:
    1. Fetch all entries across pages.
    2. Normalize, validate required fields, and deduplicate.
    3. Verify and print diagnostics.
    4. Atomically publish anime.json.
    """
    raw_entries, visited_offsets, strategy = fetch_all_mal_entries(
        username=username,
        use_status_7=True,
        delay=delay,
        timeout=timeout,
        max_retries=max_retries,
        backoff_factor=backoff_factor,
        session=session,
    )

    processed_anime, duplicate_count, metadata_stats, malformed_entries = process_raw_entries(raw_entries)

    if malformed_entries:
        logger.error(f"Encountered {len(malformed_entries)} entries with malformed required fields:")
        for aid, reason in malformed_entries[:10]:
            logger.error(f"  - Anime ID: {aid}: {reason}")
        if len(malformed_entries) > 10:
            logger.error(f"  ... and {len(malformed_entries) - 10} more malformed entries.")
        raise ValidationError(
            f"Dataset contains {len(malformed_entries)} malformed entries. Refusing to publish incomplete data."
        )

    # Status counts
    status_counts = collections.Counter(item["status"] for item in processed_anime)
    unique_ids = {item["id"] for item in processed_anime}

    # Diagnostics summary
    print_summary(
        raw_count=len(raw_entries),
        unique_count=len(processed_anime),
        visited_offsets=visited_offsets,
        strategy=strategy,
        status_counts=status_counts,
        duplicate_count=duplicate_count,
        metadata_stats=metadata_stats,
        existing_file=output_file,
        new_anime_list=processed_anime,
    )

    # Atomically save
    save_anime_json_atomically(
        anime_list=processed_anime,
        output_file=output_file,
        expected_unique_ids=unique_ids,
    )

    return processed_anime


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate anime.json from MyAnimeList with robust pagination.")
    parser.add_argument("--username", default=DEFAULT_USERNAME, help=f"MAL username (default: {DEFAULT_USERNAME})")
    parser.add_argument("--output", default=DEFAULT_OUTPUT_FILE, help=f"Output JSON path (default: {DEFAULT_OUTPUT_FILE})")
    parser.add_argument("--delay", type=float, default=DEFAULT_REQUEST_DELAY, help=f"Delay between requests in seconds (default: {DEFAULT_REQUEST_DELAY})")
    parser.add_argument("--timeout", type=float, default=DEFAULT_REQUEST_TIMEOUT, help=f"Request timeout in seconds (default: {DEFAULT_REQUEST_TIMEOUT})")
    parser.add_argument("--retries", type=int, default=DEFAULT_MAX_RETRIES, help=f"Max retries per page (default: {DEFAULT_MAX_RETRIES})")
    parser.add_argument("--backoff", type=float, default=DEFAULT_BACKOFF_FACTOR, help=f"Backoff multiplier (default: {DEFAULT_BACKOFF_FACTOR})")

    args = parser.parse_args()

    try:
        generate(
            username=args.username,
            output_file=args.output,
            delay=args.delay,
            timeout=args.timeout,
            max_retries=args.retries,
            backoff_factor=args.backoff,
        )
        return 0
    except Exception as exc:
        logger.error(f"Generation failed: {exc}", exc_info=True)
        return 1


if __name__ == "__main__":
    sys.exit(main())
