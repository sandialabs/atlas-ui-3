"""WebSocket wiring for exhausted LLM budgets (issue #1086).

The classifier is unit-tested in ``test_budget_exceeded_error``; this pins that
the transport emits the budget's own error frame and metric instead of folding
it into a rate-limit, bad-request, or generic domain error.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from main import app

from atlas.application.chat.runs import reset_run_registry
from atlas.domain.errors import LLMBudgetExceededError

USER = "owner@example.com"

BUDGET_MESSAGE = (
    "The spending budget for this model or team has been used up. "
    "Choose a different model or team, or contact your administrator."
)


@pytest.fixture
def chat_service():
    with patch("main.app_factory") as mock_factory:
        cfg = MagicMock()
        cfg.app_settings.test_user = None
        cfg.app_settings.debug_mode = False
        cfg.app_settings.auth_user_header = "X-User-Email"
        cfg.app_settings.auth_user_header_type = "header"
        cfg.app_settings.auth_aws_expected_alb_arn = ""
        cfg.app_settings.auth_aws_region = "us-east-1"
        cfg.app_settings.feature_proxy_secret_enabled = False
        cfg.app_settings.feature_websocket_origin_check_enabled = False
        cfg.app_settings.max_concurrent_runs_per_user = 3
        mock_factory.get_config_manager.return_value = cfg

        service = MagicMock()
        service.handle_chat_message = AsyncMock(return_value={})
        service.end_session = AsyncMock()
        service.session_repository.get = AsyncMock(return_value=None)
        service.steering_classification_refusal = AsyncMock(return_value=None)
        mock_factory.create_chat_service.return_value = service
        reset_run_registry()
        yield service
        reset_run_registry()


def _connect(client):
    return client.websocket_connect("/ws", headers={"X-User-Email": USER})


def test_budget_refusal_reports_its_own_error_type(chat_service):
    chat_service.handle_chat_message = AsyncMock(
        side_effect=LLMBudgetExceededError(BUDGET_MESSAGE)
    )

    with patch("main.log_metric") as log_metric:
        with _connect(TestClient(app)) as ws:
            ws.send_json({"type": "chat", "content": "synthetic", "model": "m"})
            frame = ws.receive_json()
            while frame.get("type") != "error":
                frame = ws.receive_json()

    assert frame["error_type"] == "budget_exceeded"
    assert frame["message"] == BUDGET_MESSAGE
    log_metric.assert_any_call("error", USER, error_type="budget_exceeded")
