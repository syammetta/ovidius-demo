"""Tests for R2 document storage."""

import pytest
from unittest.mock import MagicMock, patch
from botocore.exceptions import ClientError

from app.storage import store_document, get_document, document_exists, list_documents, _url_to_key


class TestUrlToKey:
    def test_produces_consistent_key(self):
        url = "https://www.irs.gov/publications/p501"
        key1 = _url_to_key(url)
        key2 = _url_to_key(url)
        assert key1 == key2

    def test_different_urls_different_keys(self):
        key1 = _url_to_key("https://www.irs.gov/publications/p501")
        key2 = _url_to_key("https://www.irs.gov/publications/p502")
        assert key1 != key2

    def test_key_starts_with_raw_prefix(self):
        key = _url_to_key("https://example.com/page")
        assert key.startswith("raw/")

    def test_key_ends_with_html(self):
        key = _url_to_key("https://example.com/page")
        assert key.endswith(".html")

    def test_strips_protocol(self):
        key = _url_to_key("https://example.com/page")
        assert "https://" not in key

    def test_long_url_truncated(self):
        url = "https://example.com/" + "a" * 200
        key = _url_to_key(url)
        assert len(key) < 200


class TestStoreDocument:
    def test_stores_content(self, mock_r2):
        with patch("app.storage.settings") as mock_settings:
            mock_settings.r2_bucket_name = "test-bucket"
            mock_settings.r2_account_id = "test-account"

            key = store_document("https://example.com", "<html>content</html>")

        mock_r2.put_object.assert_called_once()
        call_kwargs = mock_r2.put_object.call_args.kwargs
        assert call_kwargs["Bucket"] == "test-bucket"
        assert call_kwargs["ContentType"] == "text/html"
        assert b"content" in call_kwargs["Body"]

    def test_stores_metadata(self, mock_r2):
        with patch("app.storage.settings") as mock_settings:
            mock_settings.r2_bucket_name = "test-bucket"
            mock_settings.r2_account_id = "test-account"

            store_document("https://example.com", "content", metadata={"page-type": "publication"})

        call_kwargs = mock_r2.put_object.call_args.kwargs
        assert "source-url" in call_kwargs["Metadata"]
        assert "crawled-at" in call_kwargs["Metadata"]
        assert call_kwargs["Metadata"]["page-type"] == "publication"


class TestGetDocument:
    def test_returns_cached_content(self, mock_r2):
        mock_r2.get_object.return_value = {
            "Body": MagicMock(read=MagicMock(return_value=b"<html>cached</html>"))
        }

        with patch("app.storage.settings") as mock_settings:
            mock_settings.r2_bucket_name = "test-bucket"
            mock_settings.r2_account_id = "test-account"
            result = get_document("https://example.com")

        assert result == "<html>cached</html>"

    def test_returns_none_for_missing(self, mock_r2):
        mock_r2.get_object.side_effect = ClientError(
            {"Error": {"Code": "NoSuchKey", "Message": "Not found"}},
            "GetObject",
        )

        with patch("app.storage.settings") as mock_settings:
            mock_settings.r2_bucket_name = "test-bucket"
            mock_settings.r2_account_id = "test-account"
            result = get_document("https://example.com/nonexistent")

        assert result is None

    def test_raises_on_other_errors(self, mock_r2):
        mock_r2.get_object.side_effect = ClientError(
            {"Error": {"Code": "AccessDenied", "Message": "Forbidden"}},
            "GetObject",
        )

        with patch("app.storage.settings") as mock_settings:
            mock_settings.r2_bucket_name = "test-bucket"
            mock_settings.r2_account_id = "test-account"

            with pytest.raises(ClientError):
                get_document("https://example.com")


class TestDocumentExists:
    def test_returns_true_when_exists(self, mock_r2):
        mock_r2.head_object.return_value = {}

        with patch("app.storage.settings") as mock_settings:
            mock_settings.r2_bucket_name = "test-bucket"
            mock_settings.r2_account_id = "test-account"
            assert document_exists("https://example.com") is True

    def test_returns_false_when_missing(self, mock_r2):
        mock_r2.head_object.side_effect = ClientError(
            {"Error": {"Code": "404", "Message": "Not found"}}, "HeadObject"
        )

        with patch("app.storage.settings") as mock_settings:
            mock_settings.r2_bucket_name = "test-bucket"
            mock_settings.r2_account_id = "test-account"
            assert document_exists("https://example.com/missing") is False


class TestListDocuments:
    def test_returns_document_list(self, mock_r2):
        from datetime import datetime
        mock_r2.list_objects_v2.return_value = {
            "Contents": [
                {"Key": "raw/page1.html", "Size": 1024, "LastModified": datetime(2025, 1, 1)},
                {"Key": "raw/page2.html", "Size": 2048, "LastModified": datetime(2025, 1, 2)},
            ]
        }

        with patch("app.storage.settings") as mock_settings:
            mock_settings.r2_bucket_name = "test-bucket"
            mock_settings.r2_account_id = "test-account"
            result = list_documents()

        assert len(result) == 2
        assert result[0]["key"] == "raw/page1.html"

    def test_returns_empty_for_no_contents(self, mock_r2):
        mock_r2.list_objects_v2.return_value = {}

        with patch("app.storage.settings") as mock_settings:
            mock_settings.r2_bucket_name = "test-bucket"
            mock_settings.r2_account_id = "test-account"
            result = list_documents()

        assert result == []
