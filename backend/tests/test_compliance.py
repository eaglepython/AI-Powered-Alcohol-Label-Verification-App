"""
Tests for the TTB compliance engine — pure logic, no external API calls.
Run with: pytest backend/tests/ -v
"""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from main import (
    verify_government_warning,
    verify_alcohol_content,
    fuzzy_match,
    run_compliance_checks,
    GOVERNMENT_WARNING_EXACT,
)


# ── Government Warning Tests ──────────────────────────────────

class TestGovernmentWarning:
    def test_exact_warning_passes(self):
        ok, msg = verify_government_warning(GOVERNMENT_WARNING_EXACT, "bold, all caps, legible")
        assert ok is True

    def test_missing_warning_fails(self):
        ok, msg = verify_government_warning("NOT FOUND", "")
        assert ok is False
        assert "missing" in msg.lower()

    def test_empty_warning_fails(self):
        ok, msg = verify_government_warning("", "")
        assert ok is False

    def test_title_case_prefix_fails(self):
        """Jenny's exact example: 'Government Warning:' instead of 'GOVERNMENT WARNING:'"""
        warning = GOVERNMENT_WARNING_EXACT.replace("GOVERNMENT WARNING:", "Government Warning:")
        ok, msg = verify_government_warning(warning, "legible")
        assert ok is False
        assert "ALL CAPS" in msg or "caps" in msg.lower()

    def test_lowercase_prefix_fails(self):
        warning = GOVERNMENT_WARNING_EXACT.replace("GOVERNMENT WARNING:", "government warning:")
        ok, msg = verify_government_warning(warning, "legible")
        assert ok is False

    def test_missing_surgeon_general_fails(self):
        warning = "GOVERNMENT WARNING: Drinking is bad for you."
        ok, msg = verify_government_warning(warning, "legible")
        assert ok is False
        assert "surgeon general" in msg.lower() or "missing required language" in msg.lower()

    def test_missing_birth_defects_fails(self):
        warning = "GOVERNMENT WARNING: (1) According to the Surgeon General, avoid alcohol. (2) impairs drive a car or operate machinery, and may cause health problems."
        ok, msg = verify_government_warning(warning, "legible")
        assert ok is False

    def test_tiny_font_flagged(self):
        ok, msg = verify_government_warning(GOVERNMENT_WARNING_EXACT, "very small font, buried at bottom")
        assert ok is False
        assert "font" in msg.lower() or "size" in msg.lower()


# ── Alcohol Content Tests ─────────────────────────────────────

class TestAlcoholContent:
    def test_valid_abv_passes(self):
        ok, _ = verify_alcohol_content("45% Alc./Vol.")
        assert ok is True

    def test_valid_with_proof_passes(self):
        ok, _ = verify_alcohol_content("40% Alc./Vol. (80 Proof)")
        assert ok is True

    def test_missing_fails(self):
        ok, msg = verify_alcohol_content("NOT FOUND")
        assert ok is False
        assert "not found" in msg.lower()

    def test_empty_fails(self):
        ok, msg = verify_alcohol_content("")
        assert ok is False

    def test_no_percent_sign_fails(self):
        ok, msg = verify_alcohol_content("forty percent alcohol")
        assert ok is False

    def test_zero_percent_fails(self):
        ok, msg = verify_alcohol_content("0% Alc./Vol.")
        assert ok is False
        assert "invalid" in msg.lower()

    def test_over_100_fails(self):
        ok, msg = verify_alcohol_content("105% Alc./Vol.")
        assert ok is False

    def test_low_abv_beer_passes(self):
        ok, _ = verify_alcohol_content("4.2% Alc./Vol.")
        assert ok is True


# ── Fuzzy Match Tests (Dave's "STONE'S THROW" case) ──────────

class TestFuzzyMatch:
    def test_exact_match(self):
        assert fuzzy_match("OLD TOM DISTILLERY", "OLD TOM DISTILLERY") is True

    def test_case_insensitive(self):
        """Dave's scenario: form has all-caps, label has title case"""
        assert fuzzy_match("STONE'S THROW", "Stone's Throw") is True

    def test_apostrophe_variants(self):
        """Smart quotes vs straight quotes"""
        assert fuzzy_match("STONE\u2019S THROW", "STONE'S THROW") is True
        assert fuzzy_match("STONE`S THROW", "STONE'S THROW") is True

    def test_extra_whitespace(self):
        assert fuzzy_match("OLD  TOM  DISTILLERY", "OLD TOM DISTILLERY") is True

    def test_different_names_dont_match(self):
        assert fuzzy_match("OLD TOM DISTILLERY", "NEW TOM DISTILLERY") is False

    def test_empty_strings(self):
        assert fuzzy_match("", "") is True

    def test_single_char_difference_fails(self):
        assert fuzzy_match("STONE'S THROW", "STONES THROW") is False


# ── Full Compliance Check Tests ───────────────────────────────

class TestRunComplianceChecks:
    def _full_valid_extracted(self):
        return {
            "brand_name": "OLD TOM DISTILLERY",
            "class_type": "Kentucky Straight Bourbon Whiskey",
            "alcohol_content": "45% Alc./Vol. (90 Proof)",
            "net_contents": "750 mL",
            "producer_name": "Old Tom Distillery, Louisville, KY",
            "country_of_origin": "USA",
            "government_warning": GOVERNMENT_WARNING_EXACT,
            "warning_format": "bold, all caps, clearly legible",
            "image_quality": "GOOD",
            "extraction_confidence": 0.95,
        }

    def test_fully_compliant_label_approved(self):
        fields, issues, recs = run_compliance_checks(self._full_valid_extracted())
        assert issues == []
        assert fields["brand_name"]["compliant"] is True
        assert fields["government_warning"]["compliant"] is True
        assert fields["alcohol_content"]["compliant"] is True

    def test_missing_brand_name_creates_issue(self):
        data = self._full_valid_extracted()
        data["brand_name"] = "NOT FOUND"
        fields, issues, _ = run_compliance_checks(data)
        assert fields["brand_name"]["compliant"] is False
        assert any("brand" in i.lower() for i in issues)

    def test_missing_government_warning_creates_issue(self):
        data = self._full_valid_extracted()
        data["government_warning"] = "NOT FOUND"
        fields, issues, _ = run_compliance_checks(data)
        assert fields["government_warning"]["compliant"] is False
        assert len(issues) > 0

    def test_missing_net_contents_creates_issue(self):
        data = self._full_valid_extracted()
        data["net_contents"] = "NOT FOUND"
        fields, issues, _ = run_compliance_checks(data)
        assert fields["net_contents"]["compliant"] is False
        assert any("net contents" in i.lower() or "volume" in i.lower() for i in issues)

    def test_poor_image_quality_adds_recommendation(self):
        data = self._full_valid_extracted()
        data["image_quality"] = "POOR — significant glare on right side"
        _, _, recs = run_compliance_checks(data)
        assert any("image quality" in r.lower() for r in recs)

    def test_country_of_origin_always_present(self):
        """Country of origin field should always appear (even as NOT APPLICABLE)"""
        fields, _, _ = run_compliance_checks(self._full_valid_extracted())
        assert "country_of_origin" in fields

    def test_all_required_fields_present_in_output(self):
        fields, _, _ = run_compliance_checks(self._full_valid_extracted())
        required = ["brand_name", "class_type", "alcohol_content", "net_contents",
                    "producer_name", "government_warning"]
        for field in required:
            assert field in fields, f"Missing field in output: {field}"
