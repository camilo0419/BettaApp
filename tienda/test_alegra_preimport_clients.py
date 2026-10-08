from types import SimpleNamespace
from unittest.mock import Mock

from django.test import SimpleTestCase, TestCase

from tienda.models import Cliente, ExternalObjectMap
from tienda.services.alegra_client import AlegraHTTPError, AlegraResponse
from tienda.services.alegra_preimport_clients import (
    ACTION_CREATE_LOCAL,
    ACTION_LINK_EXISTING,
    ACTION_NO_ACTION,
    ACTION_REVIEW_CONFLICT,
    ACTION_REVIEW_DUPLICATE,
    ACTION_SKIP_INVALID,
    build_preimport_plan,
    fetch_customer_contacts,
)
from tienda.services.alegra_import import get_alegra_system


class FakePagedClient:
    def __init__(self, pages):
        self.pages = pages
        self.calls = []

    def get(self, path, params):
        self.calls.append((path, params.copy()))
        index = len(self.calls) - 1
        value = self.pages[min(index, len(self.pages) - 1)]
        if isinstance(value, Exception):
            raise value
        return AlegraResponse(200, value, "https://example.test/contacts")


class AlegraPreimportFetchTests(SimpleTestCase):
    def test_reaches_end_only_after_empty_page_when_total_is_unknown(self):
        client = FakePagedClient([
            [{"id": "1", "name": "Uno", "identification": "1", "type": ["client"]}],
            [],
        ])
        result = fetch_customer_contacts(client, limit=10, max_pages=5)
        self.assertEqual(result["coverage"], "complete")
        self.assertEqual(result["stopped_reason"], "end_of_pagination")
        self.assertEqual(len(result["rows"]), 1)
        self.assertEqual(len(client.calls), 2)

    def test_all_mode_does_not_use_artificial_limit(self):
        page = [{"id": str(i), "name": f"C{i}", "identification": str(i), "type": ["client"]} for i in range(30)]
        client = FakePagedClient([page, [{"id": "31", "name": "Final", "identification": "31", "type": ["client"]}], []])
        result = fetch_customer_contacts(client, limit=None, max_pages=None)
        self.assertEqual(result["coverage"], "complete")
        self.assertEqual(len(result["rows"]), 31)
        self.assertEqual(result["pages"], 3)

    def test_limit_and_max_pages_are_explicit(self):
        page = [{"id": str(i), "name": f"C{i}", "identification": str(i), "type": ["client"]} for i in range(30)]
        result = fetch_customer_contacts(FakePagedClient([page, page]), limit=30, max_pages=2)
        self.assertEqual(result["coverage"], "limited")
        self.assertEqual(result["stopped_reason"], "limit")

    def test_repeated_page_stops_without_loop(self):
        page = [{"id": "1", "name": "Uno", "identification": "1", "type": ["client"]}]
        result = fetch_customer_contacts(FakePagedClient([page, page]), limit=10, max_pages=5)
        self.assertEqual(result["stopped_reason"], "repeated_page")
        self.assertEqual(result["coverage"], "incomplete")

    def test_retries_rate_limit_and_deduplicates_external_ids(self):
        client = Mock()
        client.get.side_effect = [
            AlegraHTTPError(429, "rate limited"),
            AlegraResponse(200, [{"id": "1", "name": "Uno", "identification": "1", "type": ["client"]}], "url"),
            AlegraResponse(200, [], "url"),
        ]
        result = fetch_customer_contacts(client, limit=10, max_pages=5, max_retries=1)
        self.assertEqual(result["retries_429"], 1)
        self.assertEqual(result["duplicate_external_ids"], 0)
        self.assertEqual(len(result["rows"]), 1)
        self.assertEqual(client.get.call_count, 3)

    def test_http_error_is_incomplete(self):
        client = FakePagedClient([AlegraHTTPError(503, "unavailable")])
        result = fetch_customer_contacts(client, limit=10, max_pages=2)
        self.assertEqual(result["coverage"], "incomplete")
        self.assertEqual(result["stopped_reason"], "error")
        self.assertEqual(len(result["errors"]), 1)


class AlegraPreimportPlanTests(TestCase):
    def test_plan_classifies_without_writes(self):
        local = Cliente.objects.create(nombre="Local", identificacion="900111222")
        match_local = Cliente.objects.create(nombre="Coincidencia", identificacion="900111223")
        system = get_alegra_system()
        content_type = ExternalObjectMap._meta.get_field("content_type").remote_field.model.objects.get_for_model(Cliente)
        mapping = ExternalObjectMap.objects.create(
            system=system,
            resource_type="contacts",
            external_id="linked",
            content_type=content_type,
            object_id=local.pk,
        )
        rows = [
            {"id": "linked", "name": "Vinculado", "identification": "900111222", "type": ["client"]},
            {"id": "new", "name": "Nuevo", "identification": "800333444", "type": ["client"]},
            {"id": "match", "name": "Coincide", "identification": "900-111-223", "type": ["client"]},
            {"id": "provider", "name": "Proveedor", "identification": "7001", "type": ["provider"]},
            {"id": "bad", "name": "Sin documento", "type": ["client"]},
            {"id": "dup-1", "name": "Uno", "identification": "600555666", "type": ["client"]},
            {"id": "dup-2", "name": "Dos", "identification": "600-555-666", "type": ["client"]},
        ]
        before = (Cliente.objects.count(), ExternalObjectMap.objects.count())
        result = build_preimport_plan(rows, local_clients=[local, match_local], active_maps=[mapping], all_maps=[mapping])
        actions = [item["action"] for item in result["plans"]]
        self.assertEqual(actions, [ACTION_NO_ACTION, ACTION_CREATE_LOCAL, ACTION_LINK_EXISTING, ACTION_SKIP_INVALID, ACTION_SKIP_INVALID, ACTION_REVIEW_DUPLICATE, ACTION_REVIEW_DUPLICATE])
        self.assertEqual(before, (Cliente.objects.count(), ExternalObjectMap.objects.count()))

    def test_contradictory_mapping_is_conflict(self):
        maps = [SimpleNamespace(external_id="x", object_id=1), SimpleNamespace(external_id="x", object_id=2)]
        result = build_preimport_plan(
            [{"id": "x", "name": "Contacto", "identification": "9001", "type": ["client"]}],
            all_maps=maps,
        )
        self.assertEqual(result["plans"][0]["action"], ACTION_REVIEW_CONFLICT)

    def test_client_and_provider_is_kept_as_customer_candidate(self):
        result = build_preimport_plan([
            {"id": "both", "name": "Mixto", "identification": "9002", "type": ["client", "provider"]},
        ])
        self.assertEqual(result["plans"][0]["action"], ACTION_CREATE_LOCAL)
        self.assertTrue(result["plans"][0]["customer_and_provider"])
