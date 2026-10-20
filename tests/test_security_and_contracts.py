import pytest
from pydantic import ValidationError

from caselens.config import EndpointConfig, JevConfig, Settings
from caselens.errors import status_error
from caselens.schemas import Policy
from caselens.service import fingerprint


@pytest.mark.parametrize("endpoint", [
    "http://api.vendor.test/v1", "https://user:pass@api.vendor.test/v1",
    "https://api.vendor.test/v1?token=secret", "https://api.vendor.test/v1#fragment",
])
def test_remote_endpoints_cannot_send_keys_over_plain_http_or_embedded_url_secrets(endpoint):
    with pytest.raises(ValidationError):
        EndpointConfig(base_url=endpoint)


@pytest.mark.parametrize("endpoint", ["http://localhost:8000/v1", "http://127.0.0.1/v1", "https://api.vendor.test/v1"])
def test_safe_local_or_tls_endpoint_is_accepted(endpoint):
    assert EndpointConfig(base_url=endpoint).base_url == endpoint.rstrip("/")


def test_key_environment_name_is_an_explicit_allowlist():
    with pytest.raises(ValidationError):
        JevConfig(api_key_env="GITHUB_TOKEN")


def test_http_error_classes_distinguish_invalid_auth_rate_limit_and_server():
    assert status_error(401).code == "authentication"
    assert status_error(422).code == "invalid_request"
    assert status_error(429).retryable
    assert status_error(503).code == "provider_unavailable"


def test_batch_fingerprint_changes_with_scenario_and_model_configuration():
    settings, policy = Settings(), Policy()
    common = dict(settings=settings, policy=policy, goal="check", metadata={}, strategy="inspect")
    first = fingerprint(**common, mode="replay:clear")
    assert first != fingerprint(**common, mode="replay:blurred")
    settings.vlm.model = "model-v2"
    assert first != fingerprint(**common, mode="replay:clear")


def test_decision_cannot_select_a_label_below_its_reported_highest_probability():
    from caselens.schemas import ChoicePrediction
    with pytest.raises(ValidationError):
        ChoicePrediction(choice="b", probabilities={"a": .9, "b": .1})


def test_confidence_must_be_finite_and_within_documented_range():
    from caselens.schemas import ChoicePrediction
    with pytest.raises(ValidationError):
        ChoicePrediction(choice="a", probabilities={"a": 1}, confidence=1.2)
