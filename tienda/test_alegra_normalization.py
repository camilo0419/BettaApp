from django.test import SimpleTestCase

from tienda.services.alegra_normalization import (
    extract_identification_context,
    normalize_identification_type,
    normalize_kind_of_person,
    normalize_regime,
)


class AlegraNormalizationTests(SimpleTestCase):
    def test_verified_identification_aliases_are_canonical(self):
        self.assertEqual(normalize_identification_type(" CC "), "cc")
        self.assertEqual(normalize_identification_type("cedula de ciudadania"), "cc")
        self.assertEqual(normalize_identification_type("NIT"), "nit")
        self.assertEqual(normalize_kind_of_person("legal entity"), "LEGAL_ENTITY")
        self.assertEqual(normalize_regime(" common-regime "), "COMMON_REGIME")

    def test_unknown_values_remain_unconfirmed(self):
        self.assertIsNone(normalize_identification_type("CUSTOM_DOC"))
        self.assertIsNone(normalize_kind_of_person("NATURAL_PERSON"))
        self.assertIsNone(normalize_regime("CUSTOM_REGIME"))

    def test_numeric_zero_is_preserved(self):
        context = extract_identification_context({
            "verificationDigit": 0,
            "identificationObject": {"type": "NIT", "number": 900123456},
            "kindOfPerson": "LEGAL_ENTITY",
            "regime": "COMMON_REGIME",
        })
        self.assertEqual(context["dv"], "0")
        self.assertEqual(context["number"], "900123456")
