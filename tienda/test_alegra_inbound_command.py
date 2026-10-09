from unittest.mock import patch

from django.core.management import call_command, CommandError
from django.test import SimpleTestCase

from tienda.services.alegra_client import AlegraError
from tienda.services.database_lock import DatabaseLockUnavailable, advisory_lock


class InboundAlegraCommandTests(SimpleTestCase):
    def test_sqlite_lock_is_released_after_error(self):
        with self.assertRaises(RuntimeError):
            with advisory_lock("test:inbound"):
                raise RuntimeError("fallo controlado")
        with advisory_lock("test:inbound"):
            pass

    def test_lock_rejects_second_execution_in_same_process(self):
        with advisory_lock("test:inbound-busy"):
            with self.assertRaises(DatabaseLockUnavailable):
                with advisory_lock("test:inbound-busy"):
                    pass

    @patch("tienda.management.commands.alegra_contactos_entrantes.fetch_customer_contacts")
    @patch("tienda.management.commands.alegra_contactos_entrantes.AlegraReadOnlyClient")
    def test_incomplete_pagination_is_not_success(self, client_class, fetch):
        fetch.return_value = {"rows": [{"id": "1"}], "errors": [], "coverage": "limited", "pages": 1, "duplicate_external_ids": 0}
        with self.assertRaises(CommandError):
            call_command("alegra_contactos_entrantes", all=True)

    @patch("tienda.management.commands.alegra_contactos_entrantes.AlegraReadOnlyClient", side_effect=AlegraError("HTTP 429"))
    def test_api_error_is_not_success(self, client_class):
        with self.assertRaises(CommandError):
            call_command("alegra_contactos_entrantes", all=True)
