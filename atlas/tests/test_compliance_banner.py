"""Optional compliance classification banner configuration (issue #1045).

A level in compliance-levels.json may carry a presentation-only ``banner``
object: a label, background/text colors and an optional stripe pattern. These
tests pin that the shape is validated and normalized on load, that a malformed
banner is dropped without touching enforcement, and that it reaches the client
through GET /api/compliance-levels.
"""

import json

from main import app
from starlette.testclient import TestClient

from atlas.core.compliance import (
    ComplianceLevelManager,
    normalize_banner,
    normalize_banner_pattern,
)
from atlas.infrastructure.app_factory import app_factory


def _write_levels(tmp_path, levels):
    path = tmp_path / "compliance-levels.json"
    path.write_text(json.dumps({"levels": levels}))
    return path


class TestBannerParsing:
    def test_solid_banner_is_normalized(self, tmp_path):
        path = _write_levels(tmp_path, [
            {
                "name": "CUI",
                "allowed_with": ["CUI"],
                "banner": {
                    "label": "Controlled Unclassified Information",
                    "background_color": "#502B85",
                    "text_color": "#FFFFFF",
                },
            }
        ])
        manager = ComplianceLevelManager(path)

        banner = manager.get_banner("CUI")
        assert banner == {
            "label": "Controlled Unclassified Information",
            "background_color": "#502B85",
            "text_color": "#FFFFFF",
            "pattern": None,
        }

    def test_all_pattern_types_normalize(self, tmp_path):
        cases = {
            "diagonal_stripes": 40,
            "horizontal_stripes": 0,
            "edge_stripes": 45,
        }
        levels = []
        for pattern_type, angle in cases.items():
            levels.append({
                "name": pattern_type,
                "allowed_with": [pattern_type],
                "banner": {
                    "label": pattern_type,
                    "background_color": "#502B85",
                    "text_color": "#FFFFFF",
                    "pattern": {
                        "type": pattern_type,
                        "color": "#9871B9",
                        "width": 8,
                        "angle": angle,
                    },
                },
            })
        manager = ComplianceLevelManager(_write_levels(tmp_path, levels))

        for pattern_type, angle in cases.items():
            pattern = manager.get_banner(pattern_type)["pattern"]
            assert pattern["type"] == pattern_type
            assert pattern["color"] == "#9871B9"
            assert pattern["width"] == 8.0
            assert pattern["angle"] == float(angle)

    def test_banner_is_none_when_absent(self, tmp_path):
        path = _write_levels(tmp_path, [{"name": "UUR", "allowed_with": ["UUR"]}])
        manager = ComplianceLevelManager(path)

        assert manager.get_banner("UUR") is None

    def test_get_banner_resolves_aliases(self, tmp_path):
        path = _write_levels(tmp_path, [
            {
                "name": "CUI",
                "aliases": ["CUI-Basic"],
                "allowed_with": ["CUI"],
                "banner": {"label": "CUI", "background_color": "#502B85"},
            }
        ])
        manager = ComplianceLevelManager(path)

        assert manager.get_banner("CUI-Basic")["label"] == "CUI"
        assert manager.get_banner("unknown") is None


class TestBannerValidation:
    def test_background_color_required(self):
        assert normalize_banner({"label": "X"}, "X") is None
        assert normalize_banner({"label": "X", "background_color": "purple"}, "X") is None
        assert normalize_banner("not-an-object", "X") is None

    def test_label_falls_back_to_level_name(self):
        banner = normalize_banner({"background_color": "#123456"}, "SECRET")
        assert banner["label"] == "SECRET"

    def test_text_color_defaults_to_white(self):
        banner = normalize_banner({"background_color": "#123456"}, "X")
        assert banner["text_color"] == "#FFFFFF"
        banner = normalize_banner(
            {"background_color": "#123456", "text_color": "white"}, "X"
        )
        assert banner["text_color"] == "#FFFFFF"

    def test_invalid_pattern_type_falls_back_to_solid(self):
        pattern = normalize_banner_pattern(
            {"type": "zigzag", "color": "#000000", "width": 4}, "X"
        )
        assert pattern is None

    def test_invalid_pattern_color_falls_back_to_solid(self):
        assert normalize_banner_pattern({"type": "diagonal_stripes", "color": "red"}, "X") is None

    def test_stripe_width_is_bounded(self):
        assert normalize_banner_pattern(
            {"type": "diagonal_stripes", "color": "#000000", "width": 1}, "X"
        ) is None
        assert normalize_banner_pattern(
            {"type": "diagonal_stripes", "color": "#000000", "width": 33}, "X"
        ) is None
        assert normalize_banner_pattern(
            {"type": "diagonal_stripes", "color": "#000000", "width": 2}, "X"
        )["width"] == 2.0

    def test_invalid_angle_defaults(self):
        pattern = normalize_banner_pattern(
            {"type": "edge_stripes", "color": "#000000", "angle": "steep"}, "X"
        )
        assert pattern["angle"] == 45.0

    def test_rejects_bool_width(self):
        assert normalize_banner_pattern(
            {"type": "diagonal_stripes", "color": "#000000", "width": True}, "X"
        ) is None

    def test_malformed_banner_does_not_affect_enforcement(self, tmp_path):
        path = _write_levels(tmp_path, [
            {
                "name": "CUI",
                "allowed_with": ["CUI"],
                "banner": {"background_color": "nope", "pattern": "junk"},
            },
            {"name": "UUR", "allowed_with": ["UUR"]},
        ])
        manager = ComplianceLevelManager(path)

        assert manager.get_banner("CUI") is None
        # The access rule is unchanged.
        assert manager.classification_permits("UUR", ["UUR"]) is True
        assert manager.classification_permits("CUI", ["UUR"]) is False
        assert manager.classification_permits("CUI", ["CUI"]) is True


class TestBannerApi:
    def test_compliance_levels_endpoint_includes_banner(self, tmp_path, monkeypatch):
        path = _write_levels(tmp_path, [
            {
                "name": "UUR",
                "aliases": [],
                "allowed_with": ["UUR"],
                "banner": {
                    "label": "UUR",
                    "background_color": "#007A33",
                    "text_color": "#FFFFFF",
                },
            },
            {
                "name": "CUI",
                "aliases": [],
                "allowed_with": ["CUI"],
                "banner": {
                    "label": "CONTROLLED UNCLASSIFIED INFORMATION",
                    "background_color": "#502B85",
                    "text_color": "#FFFFFF",
                    "pattern": {"type": "edge_stripes", "color": "#9871B9", "width": 8},
                },
            },
        ])
        manager = ComplianceLevelManager(path)
        monkeypatch.setattr(
            "atlas.core.compliance.get_compliance_manager", lambda: manager
        )

        settings = app_factory.get_config_manager().app_settings
        resp = TestClient(app).get(
            "/api/compliance-levels", headers={"X-User-Email": settings.test_user}
        )
        assert resp.status_code == 200
        by_name = {level["name"]: level for level in resp.json()["levels"]}
        assert by_name["UUR"]["banner"]["background_color"] == "#007A33"
        assert by_name["CUI"]["banner"]["pattern"]["type"] == "edge_stripes"
        assert by_name["CUI"]["banner"]["pattern"]["width"] == 8.0

    def test_levels_without_banner_report_null(self, tmp_path, monkeypatch):
        path = _write_levels(tmp_path, [
            {"name": "Public", "allowed_with": ["Public"]},
            {"name": "Internal", "allowed_with": ["Internal"]},
        ])
        manager = ComplianceLevelManager(path)
        monkeypatch.setattr(
            "atlas.core.compliance.get_compliance_manager", lambda: manager
        )

        settings = app_factory.get_config_manager().app_settings
        resp = TestClient(app).get(
            "/api/compliance-levels", headers={"X-User-Email": settings.test_user}
        )
        assert resp.status_code == 200
        for level in resp.json()["levels"]:
            assert "banner" in level
            assert level["banner"] is None
