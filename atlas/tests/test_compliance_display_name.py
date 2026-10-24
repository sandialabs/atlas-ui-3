"""Optional compliance-level ``display_name`` (issue #1117).

A level in compliance-levels.json may carry a presentation-only friendly
label alongside its canonical ``name``. These tests pin that the field is
loaded and normalized, that it never participates in authorization or alias
resolution (a display name is not an identifier), that the banner's label
falls back through ``banner.label -> display_name -> name``, that the API
exposes ``name`` plus the resolved ``display_name``, and that history
messages spell a recorded level with its friendly label while the stored
record stays canonical.
"""

import json

from main import app
from starlette.testclient import TestClient

from atlas.core.compliance import (
    ComplianceLevelManager,
    normalize_banner,
    normalize_display_name,
)
from atlas.domain import conversation_classification as conv_class
from atlas.infrastructure.app_factory import app_factory


def _write_levels(tmp_path, levels):
    path = tmp_path / "compliance-levels.json"
    path.write_text(json.dumps({"levels": levels}))
    return path


def _manager(tmp_path, levels):
    return ComplianceLevelManager(_write_levels(tmp_path, levels))


# --- field normalization ------------------------------------------------------


class TestDisplay_nameNormalization:
    def test_accepts_a_non_empty_string_and_strips_it(self):
        assert normalize_display_name("  CUI (Restricted)  ", "X") == "CUI (Restricted)"

    def test_absent_stays_none(self):
        assert normalize_display_name(None, "X") is None

    def test_malformed_is_dropped_not_fatal(self):
        assert normalize_display_name(7, "X") is None
        assert normalize_display_name("", "X") is None
        assert normalize_display_name("   ", "X") is None
        assert normalize_display_name(True, "X") is None

    def test_malformed_logs_a_warning(self, caplog):
        normalize_display_name(7, "SECRET_LEVEL")
        assert "SECRET_LEVEL" in caplog.text


# --- loading ------------------------------------------------------------------


class TestLoading:
    def test_level_keeps_display_name_and_name(self, tmp_path):
        manager = _manager(tmp_path, [
            {
                "name": "CUI_no_ECI_no_ITAR_no_PII",
                "display_name": "CUI (Restricted)",
                "aliases": ["CUI_RESTRICTED"],
                "allowed_with": ["CUI_no_ECI_no_ITAR_no_PII"],
            },
            {"name": "Public", "allowed_with": ["Public"]},
        ])
        level = manager.levels["CUI_no_ECI_no_ITAR_no_PII"]
        assert level.display_name == "CUI (Restricted)"
        assert manager.levels["Public"].display_name is None

    def test_malformed_display_name_falls_back_to_name(self, tmp_path):
        manager = _manager(tmp_path, [
            {"name": "Internal", "display_name": "", "allowed_with": ["Internal"]},
        ])
        assert manager.levels["Internal"].display_name is None

    def test_duplicate_display_names_warn_but_load(self, tmp_path, caplog):
        manager = _manager(tmp_path, [
            {"name": "Alpha", "display_name": "Team Data", "allowed_with": ["Alpha"]},
            {"name": "Beta", "display_name": "Team Data", "allowed_with": ["Beta"]},
        ])
        assert manager.levels["Alpha"].display_name == "Team Data"
        assert manager.levels["Beta"].display_name == "Team Data"
        assert "Team Data" in caplog.text
        assert "Alpha" in caplog.text and "Beta" in caplog.text

    def test_distinct_display_names_do_not_warn(self, tmp_path, caplog):
        _manager(tmp_path, [
            {"name": "Alpha", "display_name": "One", "allowed_with": ["Alpha"]},
            {"name": "Beta", "display_name": "Two", "allowed_with": ["Beta"]},
        ])
        assert "shared by" not in caplog.text


# --- resolution ---------------------------------------------------------------


class TestGetDisplayName:
    def test_configured_label_wins(self, tmp_path):
        manager = _manager(tmp_path, [
            {
                "name": "CUI_no_ECI_no_ITAR_no_PII",
                "display_name": "CUI (Restricted)",
                "allowed_with": ["CUI_no_ECI_no_ITAR_no_PII"],
            },
        ])
        assert (
            manager.get_display_name("CUI_no_ECI_no_ITAR_no_PII") == "CUI (Restricted)"
        )

    def test_falls_back_to_canonical_name(self, tmp_path):
        manager = _manager(tmp_path, [{"name": "Internal", "allowed_with": ["Internal"]}])
        assert manager.get_display_name("Internal") == "Internal"

    def test_resolves_aliases(self, tmp_path):
        manager = _manager(tmp_path, [
            {
                "name": "CUI_no_ECI_no_ITAR_no_PII",
                "display_name": "CUI (Restricted)",
                "aliases": ["CUI_RESTRICTED"],
                "allowed_with": ["CUI_no_ECI_no_ITAR_no_PII"],
            },
        ])
        assert manager.get_display_name("CUI_RESTRICTED") == "CUI (Restricted)"

    def test_unknown_level_is_none_when_definitions_loaded(self, tmp_path):
        manager = _manager(tmp_path, [{"name": "Internal", "allowed_with": ["Internal"]}])
        assert manager.get_display_name("Retired") is None

    def test_permissive_mode_returns_the_name(self):
        manager = ComplianceLevelManager.__new__(ComplianceLevelManager)
        manager.levels = {}
        manager.mode = "explicit_allowlist"
        manager._name_to_canonical = {}
        assert manager.get_display_name("Anything") == "Anything"

    def test_none_is_none(self, tmp_path):
        manager = _manager(tmp_path, [{"name": "Internal", "allowed_with": ["Internal"]}])
        assert manager.get_display_name(None) is None


# --- display_name is presentation only ----------------------------------------


class TestAuthorizationUnchanged:
    def test_display_name_is_not_an_identifier(self, tmp_path):
        """A display name that spells another level's name must not match it."""
        manager = _manager(tmp_path, [
            {
                "name": "Alpha",
                "display_name": "Beta",
                "aliases": [],
                "allowed_with": ["Alpha"],
            },
            {"name": "Beta", "aliases": [], "allowed_with": ["Beta"]},
        ])
        assert manager.get_display_name("Alpha") == "Beta"  # the label only
        assert manager.classification_permits("Alpha", ["Beta"]) is False
        assert manager.classification_permits("Beta", ["Alpha"]) is False
        assert manager.classification_permits("Alpha", ["Alpha"]) is True

    def test_display_name_is_not_an_alias(self, tmp_path):
        manager = _manager(tmp_path, [
            {
                "name": "Alpha",
                "display_name": "Friendly",
                "aliases": ["A1"],
                "allowed_with": ["Alpha"],
            },
        ])
        # The label does not resolve as a name/alias; the alias does.
        assert manager.get_canonical_name("Friendly") is None
        assert manager.get_canonical_name("A1") == "Alpha"

    def test_stored_classifications_stay_canonical(self, tmp_path):
        manager = _manager(tmp_path, [
            {
                "name": "Alpha",
                "display_name": "Friendly",
                "allowed_with": ["Alpha"],
            },
        ])
        accepted, rejected = manager.partition_classifications(["Alpha", "Friendly"])
        assert accepted == ["Alpha"]
        assert rejected == ["Friendly"]


# --- banner fallback ----------------------------------------------------------


class TestBannerLabelFallback:
    def test_banner_label_wins_over_display_name(self):
        banner = normalize_banner(
            {"label": "Banner words", "background_color": "#123456"},
            "SECRET",
            display_name="Friendly",
        )
        assert banner["label"] == "Banner words"

    def test_banner_label_falls_back_to_display_name(self):
        banner = normalize_banner(
            {"background_color": "#123456"},
            "SECRET",
            display_name="Friendly",
        )
        assert banner["label"] == "Friendly"

    def test_banner_label_falls_back_to_level_name(self):
        banner = normalize_banner({"background_color": "#123456"}, "SECRET")
        assert banner["label"] == "SECRET"

    def test_banner_fallback_via_config(self, tmp_path):
        manager = _manager(tmp_path, [
            {
                "name": "CUI_no_ECI_no_ITAR_no_PII",
                "display_name": "CUI (Restricted)",
                "allowed_with": ["CUI_no_ECI_no_ITAR_no_PII"],
                "banner": {"background_color": "#502B85"},
            },
            {
                "name": "UUR",
                "allowed_with": ["UUR"],
                "banner": {"label": "Unclassified", "background_color": "#007A33"},
            },
        ])
        assert manager.get_banner("CUI_no_ECI_no_ITAR_no_PII")["label"] == "CUI (Restricted)"
        assert manager.get_banner("UUR")["label"] == "Unclassified"


# --- API ----------------------------------------------------------------------


class TestComplianceLevelsApi:
    def _get_levels(self, tmp_path, monkeypatch, levels):
        manager = _manager(tmp_path, levels)
        monkeypatch.setattr(
            "atlas.core.compliance.get_compliance_manager", lambda: manager
        )
        settings = app_factory.get_config_manager().app_settings
        resp = TestClient(app).get(
            "/api/compliance-levels", headers={"X-User-Email": settings.test_user}
        )
        assert resp.status_code == 200
        return {level["name"]: level for level in resp.json()["levels"]}

    def test_levels_expose_name_and_display_name(self, tmp_path, monkeypatch):
        by_name = self._get_levels(tmp_path, monkeypatch, [
            {
                "name": "CUI_no_ECI_no_ITAR_no_PII",
                "display_name": "CUI (Restricted)",
                "aliases": ["CUI_RESTRICTED"],
                "allowed_with": ["CUI_no_ECI_no_ITAR_no_PII"],
            },
            {"name": "Public", "allowed_with": ["Public"]},
        ])
        assert by_name["CUI_no_ECI_no_ITAR_no_PII"]["display_name"] == "CUI (Restricted)"
        assert by_name["CUI_no_ECI_no_ITAR_no_PII"]["name"] == "CUI_no_ECI_no_ITAR_no_PII"
        # A level without one resolves to its name.
        assert by_name["Public"]["display_name"] == "Public"


# --- history messages ---------------------------------------------------------


class TestHistoryMessages:
    def _refusal(self, tmp_path, recorded, active, display_name=None):
        level = {
            "name": "CUI_no_ECI_no_ITAR_no_PII",
            "aliases": ["CUI_RESTRICTED"],
            "allowed_with": ["CUI_no_ECI_no_ITAR_no_PII"],
        }
        if display_name:
            level["display_name"] = display_name
        manager = _manager(tmp_path, [level, {"name": "UUR", "allowed_with": ["UUR"]}])
        return conv_class.resume_refusal(
            conv_class.make_binding(conv_class.STATE_CLASSIFIED, recorded),
            active,
            compliance_enabled=True,
            compliance_mgr=manager,
        )

    def test_refusal_names_the_display_name(self, tmp_path):
        message = self._refusal(
            tmp_path,
            recorded="CUI_no_ECI_no_ITAR_no_PII",
            active="UUR",
            display_name="CUI (Restricted)",
        )
        assert "CUI (Restricted)" in message
        assert "CUI_no_ECI_no_ITAR_no_PII" not in message

    def test_refusal_falls_back_to_canonical_name(self, tmp_path):
        message = self._refusal(
            tmp_path, recorded="CUI_no_ECI_no_ITAR_no_PII", active="UUR"
        )
        assert "CUI_no_ECI_no_ITAR_no_PII" in message

    def test_refusal_resolves_a_recorded_alias(self, tmp_path):
        message = self._refusal(
            tmp_path,
            recorded="CUI_RESTRICTED",
            active="UUR",
            display_name="CUI (Restricted)",
        )
        assert "CUI (Restricted)" in message

    def test_active_level_uses_its_own_display_name(self, tmp_path):
        manager = _manager(tmp_path, [
            {"name": "UUR", "display_name": "Unclassified", "allowed_with": ["UUR"]},
            {
                "name": "CUI",
                "display_name": "CUI (Restricted)",
                "allowed_with": ["CUI"],
            },
        ])
        message = conv_class.resume_refusal(
            conv_class.make_binding(conv_class.STATE_UNCLASSIFIED),
            "UUR",
            compliance_enabled=True,
            compliance_mgr=manager,
        )
        assert "Unclassified" in message
        assert "CUI (Restricted)" not in message

    def test_undefined_recorded_level_keeps_its_stored_name(self, tmp_path):
        manager = _manager(tmp_path, [
            {"name": "UUR", "display_name": "Unclassified", "allowed_with": ["UUR"]},
        ])
        message = conv_class.resume_refusal(
            conv_class.make_binding(conv_class.STATE_CLASSIFIED, "Retired"),
            "UUR",
            compliance_enabled=True,
            compliance_mgr=manager,
        )
        assert "Retired" in message

    def test_stored_record_stays_canonical(self, tmp_path):
        """Rehydration resolves the label; the persisted record is untouched."""
        binding = conv_class.make_binding(conv_class.STATE_CLASSIFIED, "CUI")
        assert conv_class.metadata_value(binding) == "CUI"
        assert conv_class.public_fields({"data_classification": "CUI"}) == {
            "data_classification": "CUI",
            "data_classification_state": "classified",
        }
