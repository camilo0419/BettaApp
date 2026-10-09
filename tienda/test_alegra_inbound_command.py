from unittest.mock import MagicMock, patch

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

    def test_mariadb_advisory_lock_uses_get_and_release_lock(self):
        cursor = MagicMock()
        cursor.fetchone.return_value = (1,)
        cursor_context = MagicMock()
        cursor_context.__enter__.return_value = cursor
        fake_connection = MagicMock(vendor="mysql")
        fake_connection.cursor.return_value = cursor_context
        with patch("tienda.services.database_lock.connection", fake_connection):
            with advisory_lock("test:mariadb"):
                pass
        self.assertEqual(cursor.execute.call_count, 2)
        self.assertIn("GET_LOCK", cursor.execute.call_args_list[0].args[0])
        self.assertIn("RELEASE_LOCK", cursor.execute.call_args_list[1].args[0])

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
