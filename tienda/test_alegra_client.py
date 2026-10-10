from unittest.mock import Mock, patch

from django.test import SimpleTestCase

from tienda.services.alegra_client import AlegraPaginationError, AlegraReadOnlyClient, AlegraResponse


class AlegraReadOnlyClientTests(SimpleTestCase):
    def test_complete_pagination_rejects_short_page_before_declared_total(self):
        client = object.__new__(AlegraReadOnlyClient)
        client.get = Mock(return_value=AlegraResponse(
            200, {"data": [{"id": "1"}], "metadata": {"total": 3}}, "https://example.test/contacts",
        ))
        with self.assertRaises(AlegraPaginationError):
            client.paged_get("/contacts", limit=None, require_complete=True)

    def test_complete_pagination_rejects_empty_page_before_declared_total(self):
        client = object.__new__(AlegraReadOnlyClient)
        client.get = Mock(return_value=AlegraResponse(
            200, {"data": [], "metadata": {"total": 3}}, "https://example.test/contacts",
        ))
        with self.assertRaises(AlegraPaginationError):
            client.paged_get("/contacts", limit=None, require_complete=True)

    def test_limited_consumers_keep_short_page_behavior(self):
        client = object.__new__(AlegraReadOnlyClient)
        client.get = Mock(return_value=AlegraResponse(
            200, {"data": [{"id": "1"}], "metadata": {"total": 3}}, "https://example.test/items",
        ))
        responses = client.paged_get("/items", limit=2)
        self.assertEqual(len(responses), 1)

    @patch.dict("os.environ", {"ALEGRA_EMAIL": "test@example.com", "ALEGRA_API_TOKEN": "secret", "ALEGRA_BASE_URL": "https://example.test/api/v1"}, clear=False)
    @patch("tienda.services.alegra_client.urlopen")
    def test_client_only_builds_get_request(self, urlopen):
        response = urlopen.return_value.__enter__.return_value
        response.status = 200
        response.read.return_value = b"[]"
        AlegraReadOnlyClient().get("/items")
        request = urlopen.call_args.args[0]
        self.assertEqual(request.method, "GET")
        self.assertNotIn("secret", request.headers.get("Authorization", ""))

    @patch.dict("os.environ", {}, clear=True)
    def test_missing_credentials_prevent_client_creation(self):
        with self.assertRaises(Exception):
            AlegraReadOnlyClient()
