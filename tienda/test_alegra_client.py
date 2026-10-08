from unittest.mock import patch

from django.test import SimpleTestCase

from tienda.services.alegra_client import AlegraReadOnlyClient


class AlegraReadOnlyClientTests(SimpleTestCase):
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
