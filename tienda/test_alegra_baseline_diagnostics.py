from types import SimpleNamespace

from django.test import SimpleTestCase

from tienda.management.commands.alegra_conciliar_baselines import Command


class AlegraBaselineDiagnosticsTests(SimpleTestCase):
    def test_aggregates_multiple_causes_without_personal_data(self):
        candidates = [
            (
                SimpleNamespace(pk=1), None, None,
                {
                    "state": "REVIEW",
                    "reason": "Faltan campos protegidos confirmables.",
                    "missing": ["kindOfPerson", "regime"],
                    "differences": ["kindOfPerson", "regime"],
                },
            ),
            (
                SimpleNamespace(pk=2), None, None,
                {
                    "state": "REVIEW",
                    "reason": "Existen diferencias entre Alegra y BettaApp; no se fuerza el baseline.",
                    "differences": ["email", "nombre"],
                },
            ),
            (SimpleNamespace(pk=3), None, None, {"state": "SAFE", "baseline": {"email": "oculto@example.test"}}),
        ]
        diagnostics = Command._aggregate_review_causes(candidates)

        self.assertEqual(diagnostics["affected_clients"], 2)
        self.assertEqual(diagnostics["missing_field_counts"], {"kindOfPerson": 1, "regime": 1})
        self.assertEqual(diagnostics["different_field_counts"], {"email": 1, "kindOfPerson": 1, "nombre": 1, "regime": 1})
        self.assertEqual(diagnostics["exclusive_category_counts"], {
            "FIELD_DIFFERENCES": 1,
            "MISSING_PROTECTED_FIELDS": 1,
        })
        rendered = repr(diagnostics)
        self.assertNotIn("oculto@example.test", rendered)
        self.assertNotIn("Cliente", rendered)

    def test_report_contains_only_aggregates(self):
        fetched = {
            "rows": [{"id": "external-hidden"}],
            "pages": 1,
            "coverage": "complete",
            "errors": [],
            "duplicate_external_ids": 0,
        }
        counts = {"ALREADY_BASELINE": 0, "SAFE": 0, "REVIEW": 1, "MISSING_REMOTE": 0}
        diagnostics = {
            "affected_clients": 1,
            "exclusive_category_counts": {"FIELD_DIFFERENCES": 1},
            "affected_by_category": {"FIELD_DIFFERENCES": 1},
            "missing_field_counts": {},
            "different_field_counts": {"email": 1},
            "reason_counts": {"Existen diferencias entre Alegra y BettaApp; no se fuerza el baseline.": 1},
            "protected_pair_counts": {
                "regime": [{"pair": {"local": "COMMON_REGIME", "remote": "commonRegime"}, "count": 1}],
            },
        }
        report = Command._report(fetched, counts, diagnostics, 0, False)
        self.assertIn("Clientes afectados por revisión: 1", report)
        self.assertIn("email", report)
        self.assertNotIn("external-hidden", report)

    def test_protected_pair_diagnostics_are_aggregated_without_documents(self):
        candidates = [
            (
                SimpleNamespace(pk=1), None, None,
                {
                    "state": "REVIEW",
                    "reason": "Existen diferencias.",
                    "differences": ["regime"],
                    "diagnostic": {
                        "regime": {
                            "local": "COMMON_REGIME",
                            "remote": "commonRegime",
                            "local_normalized": "COMMON_REGIME",
                            "remote_normalized": "",
                        },
                        "digito_verificacion": {"local_state": "zero", "remote_state": "present"},
                    },
                },
            ),
        ]
        diagnostics = Command._aggregate_review_causes(candidates)
        self.assertEqual(diagnostics["protected_pair_counts"]["regime"][0]["count"], 1)
        self.assertEqual(diagnostics["protected_pair_counts"]["digito_verificacion"][0]["pair"]["local_state"], "zero")
        rendered = repr(diagnostics)
        self.assertNotIn("101905", rendered)
        self.assertNotIn("correo", rendered)
