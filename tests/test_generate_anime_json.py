import json
import os
import shutil
import tempfile
import unittest
from unittest.mock import MagicMock, patch

import requests

from generate_anime_json import (
    MALFetchError,
    PaginationLoopError,
    ValidationError,
    fetch_all_mal_entries,
    fetch_page,
    fetch_status_pages,
    generate,
    normalize_entry,
    process_image_url,
    process_raw_entries,
    save_anime_json_atomically,
    validate_dataset,
)


def make_raw_anime(
    anime_id: int,
    title: str = "Test Title",
    status: int = 2,
    score: int = 8,
    image: str = "https://cdn.myanimelist.net/r/192x272/images/anime/10/1000.jpg?s=abcdef",
) -> dict:
    return {
        "anime_id": anime_id,
        "anime_title": f"{title} {anime_id}",
        "status": status,
        "score": score,
        "anime_image_path": image,
    }


class TestAnimeGenerator(unittest.TestCase):

    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.output_file = os.path.join(self.test_dir, "anime.json")

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    # -----------------------------------------------------------------------
    # Requirement 7.1: Multi-page (> 300 entries)
    # -----------------------------------------------------------------------
    def test_multi_page_more_than_300_entries(self):
        """Test retrieving more than 300 entries across multiple pages."""
        page_1 = [make_raw_anime(i) for i in range(1, 301)]
        page_2 = [make_raw_anime(i) for i in range(301, 351)]
        page_3 = []

        mock_session = MagicMock(spec=requests.Session)
        mock_resp_1 = MagicMock(status_code=200)
        mock_resp_1.json.return_value = page_1
        mock_resp_2 = MagicMock(status_code=200)
        mock_resp_2.json.return_value = page_2
        mock_resp_3 = MagicMock(status_code=200)
        mock_resp_3.json.return_value = page_3

        mock_session.get.side_effect = [mock_resp_1, mock_resp_2, mock_resp_3]

        entries, offsets = fetch_status_pages(
            session=mock_session,
            username="TestUser",
            status=7,
            delay=0,
        )

        self.assertEqual(len(entries), 350)
        self.assertEqual(offsets, [0, 300, 350])
        self.assertEqual(mock_session.get.call_count, 3)

    # -----------------------------------------------------------------------
    # Requirement 7.2: Final partial page followed by an empty page
    # -----------------------------------------------------------------------
    def test_final_partial_page_followed_by_empty_page(self):
        """Test partial page advances offset by actual count, followed by empty page."""
        page_1 = [make_raw_anime(i) for i in range(1, 151)]
        page_2 = []

        mock_session = MagicMock(spec=requests.Session)
        mock_resp_1 = MagicMock(status_code=200)
        mock_resp_1.json.return_value = page_1
        mock_resp_2 = MagicMock(status_code=200)
        mock_resp_2.json.return_value = page_2

        mock_session.get.side_effect = [mock_resp_1, mock_resp_2]

        entries, offsets = fetch_status_pages(
            session=mock_session,
            username="TestUser",
            status=7,
            delay=0,
        )

        self.assertEqual(len(entries), 150)
        self.assertEqual(offsets, [0, 150])

    # -----------------------------------------------------------------------
    # Requirement 7.3: Duplicate IDs across fetched results
    # -----------------------------------------------------------------------
    def test_duplicate_ids_across_results(self):
        """Test that duplicate IDs across fetched pages are deduplicated and logged."""
        raw_list = [
            make_raw_anime(101, "Anime A"),
            make_raw_anime(102, "Anime B"),
            make_raw_anime(101, "Anime A Duplicate"),
            make_raw_anime(103, "Anime C"),
            make_raw_anime(102, "Anime B Duplicate"),
        ]

        with self.assertLogs("anime_generator", level="WARNING") as log_capture:
            unique_list, dup_count, _, malformed = process_raw_entries(raw_list)

        self.assertEqual(len(malformed), 0)
        self.assertEqual(dup_count, 2)
        self.assertEqual(len(unique_list), 3)
        self.assertEqual([x["id"] for x in unique_list], [101, 102, 103])
        self.assertTrue(any("Duplicate anime ID 101" in msg for msg in log_capture.output))
        self.assertTrue(any("Duplicate anime ID 102" in msg for msg in log_capture.output))

    # -----------------------------------------------------------------------
    # Requirement 7.4: Missing or malformed poster URLs
    # -----------------------------------------------------------------------
    def test_missing_or_malformed_poster_urls(self):
        """Test safe handling of missing, malformed, placeholder, or extensionless poster URLs."""
        # 1. Normal thumbnail image URL with /r/192x272 and query string
        url1, valid1 = process_image_url(
            "https://cdn.myanimelist.net/r/192x272/images/anime/1867/144672.jpg?s=abc1234"
        )
        self.assertEqual(url1, "https://cdn.myanimelist.net/images/anime/1867/144672l.jpg")
        self.assertTrue(valid1)

        # 2. Already high-resolution image URL (already has 'l')
        url2, valid2 = process_image_url("https://cdn.myanimelist.net/images/anime/1867/144672l.jpg")
        self.assertEqual(url2, "https://cdn.myanimelist.net/images/anime/1867/144672l.jpg")
        self.assertTrue(valid2)

        # 3. URL without file extension (must not crash)
        url3, valid3 = process_image_url("https://cdn.myanimelist.net/images/anime/1867/144672")
        self.assertEqual(url3, "https://cdn.myanimelist.net/images/anime/1867/144672")
        self.assertTrue(valid3)

        # 4. None / empty / whitespace
        url4, valid4 = process_image_url(None)
        self.assertEqual(url4, "")
        self.assertFalse(valid4)

        url5, valid5 = process_image_url("")
        self.assertEqual(url5, "")
        self.assertFalse(valid5)

        url6, valid6 = process_image_url("   ")
        self.assertEqual(url6, "")
        self.assertFalse(valid6)

        # 5. Placeholder image (qm_50.gif)
        url7, valid7 = process_image_url("https://cdn.myanimelist.net/images/qm_50.gif")
        self.assertEqual(url7, "https://cdn.myanimelist.net/images/qm_50.gif")
        self.assertFalse(valid7)  # Treated as missing/placeholder optional metadata

        # 6. Malformed non-URL string
        url8, valid8 = process_image_url("not_a_valid_url")
        self.assertFalse(valid8)

        # Verify entry normalization doesn't drop anime with missing poster
        entry_without_poster = make_raw_anime(555, image="")
        normalized, err, flags = normalize_entry(entry_without_poster)
        self.assertIsNone(err)
        self.assertIsNotNone(normalized)
        self.assertEqual(normalized["id"], 555)
        self.assertEqual(normalized["image"], "")
        self.assertTrue(flags["missing_poster"])

    # -----------------------------------------------------------------------
    # Requirement 7.5: Transient request errors followed by a successful retry
    # -----------------------------------------------------------------------
    def test_transient_request_errors_followed_by_retry(self):
        """Test that transient network errors and 5xx responses retry and succeed."""
        mock_session = MagicMock(spec=requests.Session)

        # 1st attempt: ConnectionError
        # 2nd attempt: HTTP 503
        # 3rd attempt: 200 OK
        resp_503 = MagicMock(status_code=503)
        resp_200 = MagicMock(status_code=200)
        resp_200.json.return_value = [make_raw_anime(999)]

        mock_session.get.side_effect = [
            requests.exceptions.ConnectionError("Connection reset"),
            resp_503,
            resp_200,
        ]

        with patch("time.sleep", return_value=None):
            result = fetch_page(
                session=mock_session,
                username="TestUser",
                status=7,
                offset=0,
                max_retries=3,
                backoff_factor=1.0,
            )

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["anime_id"], 999)
        self.assertEqual(mock_session.get.call_count, 3)

    # -----------------------------------------------------------------------
    # Requirement 7.6: Unrecoverable request failure
    # -----------------------------------------------------------------------
    def test_unrecoverable_request_failure(self):
        """Test that exhausting retries raises MALFetchError."""
        mock_session = MagicMock(spec=requests.Session)
        mock_session.get.side_effect = requests.exceptions.Timeout("Read timeout")

        with patch("time.sleep", return_value=None):
            with self.assertRaises(MALFetchError):
                fetch_page(
                    session=mock_session,
                    username="TestUser",
                    status=7,
                    offset=0,
                    max_retries=3,
                    backoff_factor=1.0,
                )

        self.assertEqual(mock_session.get.call_count, 3)

    # -----------------------------------------------------------------------
    # Requirement 7.7: Invalid JSON or invalid API response
    # -----------------------------------------------------------------------
    def test_invalid_json_or_unexpected_response(self):
        """Test that invalid JSON and non-list responses trigger retries and error handling."""
        mock_session = MagicMock(spec=requests.Session)

        # Response with invalid JSON text (e.g. HTML error page with status 200)
        resp_invalid_json = MagicMock(status_code=200)
        resp_invalid_json.json.side_effect = json.JSONDecodeError("Expecting value", "doc", 0)

        # Response returning a dict instead of a list (e.g. {"error": "bad request"})
        resp_dict = MagicMock(status_code=200)
        resp_dict.json.return_value = {"error": "rate limit reached"}

        mock_session.get.side_effect = [resp_invalid_json, resp_dict, resp_dict]

        with patch("time.sleep", return_value=None):
            with self.assertRaises(MALFetchError):
                fetch_page(
                    session=mock_session,
                    username="TestUser",
                    status=7,
                    offset=0,
                    max_retries=3,
                    backoff_factor=1.0,
                )

    # -----------------------------------------------------------------------
    # Requirement 7.8: Failed generation never overwrites previous valid JSON
    # -----------------------------------------------------------------------
    def test_failed_generation_preserves_existing_json(self):
        """Test that if an error occurs at any point, existing anime.json is preserved untouched."""
        existing_data = [
            {
                "id": 1,
                "title": "Existing Anime 1",
                "image": "https://example.com/1.jpg",
                "url": "https://myanimelist.net/anime/1",
                "score": 10,
                "status": 2,
            }
        ]
        with open(self.output_file, "w", encoding="utf-8") as f:
            json.dump(existing_data, f)

        mock_session = MagicMock(spec=requests.Session)
        # Mock failure during fetch
        mock_session.get.side_effect = requests.exceptions.ConnectionError("Server unreachable")

        with patch("time.sleep", return_value=None):
            with self.assertRaises(MALFetchError):
                generate(
                    username="TestUser",
                    output_file=self.output_file,
                    delay=0,
                    max_retries=2,
                    session=mock_session,
                )

        # Verify existing file content remains completely intact
        with open(self.output_file, "r", encoding="utf-8") as f:
            after_failure = json.load(f)

        self.assertEqual(after_failure, existing_data)

    def test_malformed_entry_preserves_existing_json(self):
        """Test that malformed required fields in API responses abort publishing and preserve existing file."""
        existing_data = [{"id": 1, "title": "A", "image": "", "url": "https://myanimelist.net/anime/1", "score": 0, "status": 1}]
        with open(self.output_file, "w", encoding="utf-8") as f:
            json.dump(existing_data, f)

        mock_session = MagicMock(spec=requests.Session)
        # Page 1 contains an entry with empty title, Page 2 is empty to terminate pagination
        page_1 = [
            {"anime_id": 10, "anime_title": ""},  # Empty title is malformed
        ]
        mock_resp_1 = MagicMock(status_code=200)
        mock_resp_1.json.return_value = page_1
        mock_resp_2 = MagicMock(status_code=200)
        mock_resp_2.json.return_value = []
        mock_session.get.side_effect = [mock_resp_1, mock_resp_2]

        with patch("time.sleep", return_value=None):
            with self.assertRaises(ValidationError):
                generate(
                    username="TestUser",
                    output_file=self.output_file,
                    delay=0,
                    max_retries=1,
                    session=mock_session,
                )

        # Verify existing file is preserved
        with open(self.output_file, "r", encoding="utf-8") as f:
            after_data = json.load(f)
        self.assertEqual(after_data, existing_data)

    # -----------------------------------------------------------------------
    # Requirement 7.9: All unique IDs from multi-page appear in output exactly once
    # -----------------------------------------------------------------------
    def test_all_unique_ids_appear_exactly_once(self):
        """Test that all unique IDs from multi-page responses appear in output exactly once."""
        page_1 = [make_raw_anime(i) for i in range(1, 201)]
        # Page 2 has overlapping IDs (180..200) and new IDs (201..350)
        page_2 = [make_raw_anime(i) for i in range(180, 351)]
        page_3 = []

        mock_session = MagicMock(spec=requests.Session)
        mock_resp_1 = MagicMock(status_code=200)
        mock_resp_1.json.return_value = page_1
        mock_resp_2 = MagicMock(status_code=200)
        mock_resp_2.json.return_value = page_2
        mock_resp_3 = MagicMock(status_code=200)
        mock_resp_3.json.return_value = page_3

        mock_session.get.side_effect = [mock_resp_1, mock_resp_2, mock_resp_3]

        with patch("time.sleep", return_value=None):
            result = generate(
                username="TestUser",
                output_file=self.output_file,
                delay=0,
                session=mock_session,
            )

        # Expected unique IDs: 1 to 350
        expected_ids = set(range(1, 351))
        result_ids = [x["id"] for x in result]

        self.assertEqual(len(result_ids), 350)
        self.assertEqual(set(result_ids), expected_ids)
        self.assertEqual(len(result_ids), len(set(result_ids)))

        # Also verify the written file matches
        with open(self.output_file, "r", encoding="utf-8") as f:
            written_data = json.load(f)
        self.assertEqual(len(written_data), 350)

    # -----------------------------------------------------------------------
    # Additional: Pagination loop detection
    # -----------------------------------------------------------------------
    def test_pagination_loop_detection(self):
        """Test that an API returning identical repeated pages raises PaginationLoopError."""
        page_1 = [make_raw_anime(1), make_raw_anime(2)]
        # Page 2 returns the exact same IDs
        page_2 = [make_raw_anime(1), make_raw_anime(2)]

        mock_session = MagicMock(spec=requests.Session)
        mock_resp_1 = MagicMock(status_code=200)
        mock_resp_1.json.return_value = page_1
        mock_resp_2 = MagicMock(status_code=200)
        mock_resp_2.json.return_value = page_2

        mock_session.get.side_effect = [mock_resp_1, mock_resp_2]

        with patch("time.sleep", return_value=None):
            with self.assertRaises(PaginationLoopError):
                fetch_status_pages(
                    session=mock_session,
                    username="TestUser",
                    status=7,
                    delay=0,
                )

    # -----------------------------------------------------------------------
    # Additional: Fallback to individual statuses if status=7 fails
    # -----------------------------------------------------------------------
    def test_fallback_to_individual_statuses_on_status_7_failure(self):
        """Test automatic fallback to per-status pagination when status=7 fails."""
        mock_session = MagicMock(spec=requests.Session)

        # status=7 request returns 400 or fails
        resp_fail = MagicMock(status_code=400)
        resp_fail.text = "Invalid status"

        # individual statuses return 1 item each
        def mock_get(url, params=None, **kwargs):
            status = params.get("status") if params else None
            offset = params.get("offset") if params else 0
            if status == 7:
                resp = MagicMock(status_code=400)
                resp.raise_for_status.side_effect = requests.exceptions.HTTPError("400 Bad Request")
                return resp
            # Valid status pagination
            resp = MagicMock(status_code=200)
            if offset == 0:
                resp.json.return_value = [make_raw_anime(status * 100, status=status)]
            else:
                resp.json.return_value = []
            return resp

        mock_session.get.side_effect = mock_get

        with patch("time.sleep", return_value=None):
            raw_entries, visited_offsets, strategy = fetch_all_mal_entries(
                username="TestUser",
                use_status_7=True,
                delay=0,
                max_retries=1,
                session=mock_session,
            )

        self.assertEqual(strategy, "per-status pagination")
        self.assertEqual(len(raw_entries), 5)  # 1 for each status: 1, 2, 3, 4, 6
        self.assertIn("status_1", visited_offsets)
        self.assertIn("status_2", visited_offsets)
        self.assertIn("status_3", visited_offsets)
        self.assertIn("status_4", visited_offsets)
        self.assertIn("status_6", visited_offsets)

    # -----------------------------------------------------------------------
    # Additional: Dataset validation checks
    # -----------------------------------------------------------------------
    def test_validate_dataset_checks(self):
        """Test dataset validation rules."""
        # Valid dataset
        valid = [
            {"id": 1, "title": "A", "image": "", "url": "https://myanimelist.net/anime/1", "score": 8, "status": 1}
        ]
        validate_dataset(valid)

        # Empty dataset rejected
        with self.assertRaises(ValidationError):
            validate_dataset([])

        # Duplicate ID rejected
        dup = [
            {"id": 1, "title": "A", "image": "", "url": "https://myanimelist.net/anime/1", "score": 8, "status": 1},
            {"id": 1, "title": "B", "image": "", "url": "https://myanimelist.net/anime/1", "score": 9, "status": 2},
        ]
        with self.assertRaises(ValidationError):
            validate_dataset(dup)

        # Missing required key
        missing_key = [
            {"id": 1, "title": "A", "url": "https://myanimelist.net/anime/1", "score": 8, "status": 1}
        ]
        with self.assertRaises(ValidationError):
            validate_dataset(missing_key)


if __name__ == "__main__":
    unittest.main()
