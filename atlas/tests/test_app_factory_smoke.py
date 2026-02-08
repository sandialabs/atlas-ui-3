import sys
import types


def _ensure_litellm_stub():
    if "litellm" in sys.modules:
        return
    m = types.ModuleType("litellm")
    # Attributes used at import time
    m.drop_params = True
    def _set_verbose(*args, **kwargs):
        return None
    m.set_verbose = _set_verbose
    # Names imported via `from litellm import ...`
    def completion(*args, **kwargs):
        return None
    async def acompletion(*args, **kwargs):
        class Dummy:
            class Choice:
                class Msg:
                    content = ""
                message = Msg()
            choices = [Choice()]
        return Dummy()
    m.completion = completion
    m.acompletion = acompletion
    sys.modules["litellm"] = m


def test_app_factory_accessors():
    _ensure_litellm_stub()
    from atlas.infrastructure.app_factory import app_factory
    # Accessors should return instances without raising
    assert app_factory.get_config_manager() is not None
    assert app_factory.get_llm_caller() is not None
    assert app_factory.get_mcp_manager() is not None
    assert app_factory.get_file_storage() is not None
    assert app_factory.get_file_manager() is not None

    # RAG services are None when FEATURE_RAG_ENABLED is false (default)
    rag_enabled = app_factory.get_config_manager().app_settings.feature_rag_enabled
    if rag_enabled:
        assert app_factory.get_unified_rag_service() is not None
        assert app_factory.get_rag_mcp_service() is not None
    else:
        assert app_factory.get_unified_rag_service() is None
        assert app_factory.get_rag_mcp_service() is None
