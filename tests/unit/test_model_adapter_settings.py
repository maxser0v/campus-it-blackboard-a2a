import pytest

from campus_blackboard.config import Settings
from campus_blackboard.model_adapter import OpenAICompatibleAdapter, model_from_settings


def test_live_model_settings_propagate_without_required_api_key():
    settings = Settings(
        model_mode="live",
        model_base_url="http://127.0.0.1:9000/v1",
        model_name="local-compatible-model",
        model_timeout_seconds=22.5,
        model_max_output_tokens=900,
        model_token_limit_parameter="max_completion_tokens",
    )

    adapter = model_from_settings(settings)

    assert isinstance(adapter, OpenAICompatibleAdapter)
    assert adapter.api_key is None
    assert adapter.timeout == 22.5
    assert adapter.max_output_tokens == 900
    assert adapter.token_limit_parameter == "max_completion_tokens"


@pytest.mark.parametrize(
    "kwargs",
    [
        {"model_timeout_seconds": 0},
        {"model_max_output_tokens": 0},
        {"model_token_limit_parameter": "unsupported"},
    ],
)
def test_invalid_model_limits_are_rejected(kwargs):
    with pytest.raises(ValueError):
        Settings(**kwargs)

