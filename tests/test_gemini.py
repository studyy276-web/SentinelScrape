"""Comprehensive mocked tests for Google Gemini SDK integration (Step 4.10)."""

import os
from unittest.mock import MagicMock, patch

import pytest
from google.genai import errors

from app.integrations.gemini.service import GoogleGeminiService
from app.orchestrator.context import OrchestrationContext


@pytest.fixture
def mock_genai_client():
    with patch("app.integrations.gemini.service.genai.Client") as mock_client_class:
        mock_instance = MagicMock()
        mock_client_class.return_value = mock_instance
        yield mock_instance


def test_gemini_service_initialization():
    """Verify Gemini initializes with API key."""
    service = GoogleGeminiService(api_key="TEST_API_KEY")
    assert service.api_key == "TEST_API_KEY"
    assert service.model_name == "gemini-2.5-flash"
    assert service.client is not None


def test_gemini_service_rejects_unverified_data():
    """Verify Gemini strictly enforces Trust Gate invariant."""
    service = GoogleGeminiService(api_key="TEST_KEY")
    context = OrchestrationContext(url="http://test.com")
    context.verification_result = {"passed": False}  # Not verified

    with pytest.raises(RuntimeError, match="Security violation: Attempted AI generation on unverified data."):
        service.generate_answer(context, prompt="What is the price?")


def test_gemini_service_missing_api_key_handles_gracefully():
    """Verify Gemini handles missing API keys without crashing the state machine."""
    service = GoogleGeminiService(api_key="")
    # Clear env var if set
    service.client = None
    
    context = OrchestrationContext(url="http://test.com")
    context.verification_result = {"passed": True}
    
    result_context = service.generate_answer(context, prompt="What is the price?")
    assert "Error: Gemini AI service is unavailable" in result_context.ai_answer
    assert result_context.metadata["error"] == "Gemini API key is not configured."


def test_gemini_service_builds_strict_prompt():
    """Verify Gemini prompt strictly embeds verified data, schema, and field-referenced instructions."""
    service = GoogleGeminiService(api_key="TEST_KEY")
    context = OrchestrationContext(
        url="http://test.com",
        schema={"properties": {"price": "number"}},
        ai_prompt="What is the item price?"
    )
    context.extracted_data = {"price": 19.99}

    prompt = service._build_prompt(context, user_prompt=None)
    
    assert "Verified Extracted Data" in prompt
    assert "19.99" in prompt
    assert "What is the item price?" in prompt
    assert "MUST provide field-referenced answers" in prompt
    assert "DO NOT invent, infer, or hallucinate" in prompt


def test_gemini_service_successful_generation(mock_genai_client):
    """Verify Gemini parses response properly when API succeeds."""
    mock_response = MagicMock()
    mock_response.text = "Based on the field 'price', the value is $19.99."
    mock_genai_client.models.generate_content.return_value = mock_response

    service = GoogleGeminiService(api_key="TEST_KEY")
    service.client = mock_genai_client

    context = OrchestrationContext(url="http://test.com")
    context.verification_result = {"passed": True}
    context.extracted_data = {"price": 19.99}

    result = service.generate_answer(context, prompt="What is the price?")

    assert result.ai_answer == "Based on the field 'price', the value is $19.99."
    mock_genai_client.models.generate_content.assert_called_once()
    
    call_args = mock_genai_client.models.generate_content.call_args[1]
    assert call_args["model"] == "gemini-2.5-flash"
    assert "19.99" in call_args["contents"]


def test_gemini_service_api_error_handling(mock_genai_client):
    """Verify Gemini gracefully propagates APIErrors for orchestrator to handle."""
    mock_genai_client.models.generate_content.side_effect = errors.APIError(
        "429 Too Many Requests",
        {"error": {"message": "Too Many Requests"}}
    )

    service = GoogleGeminiService(api_key="TEST_KEY")
    service.client = mock_genai_client

    context = OrchestrationContext(url="http://test.com")
    context.verification_result = {"passed": True}

    with pytest.raises(errors.APIError):
        service.generate_answer(context, prompt="Test prompt")

    assert "Gemini APIError" in context.metadata["error"]
    assert "429 Too Many Requests" in context.metadata["error"]
    assert "Error: Failed to generate AI answer" in context.ai_answer


def test_gemini_service_unexpected_error_handling(mock_genai_client):
    """Verify Gemini handles unexpected exceptions."""
    mock_genai_client.models.generate_content.side_effect = ValueError("Corrupt response format")

    service = GoogleGeminiService(api_key="TEST_KEY")
    service.client = mock_genai_client

    context = OrchestrationContext(url="http://test.com")
    context.verification_result = {"passed": True}

    with pytest.raises(ValueError):
        service.generate_answer(context, prompt="Test prompt")

    assert "Unexpected Gemini error: Corrupt response format" in context.metadata["error"]
    assert "Error: Unexpected failure" in context.ai_answer


def test_gemini_service_caches_successful_response(mock_genai_client):
    """Verify that a successful Gemini generation populates the cache."""
    mock_response = MagicMock()
    mock_response.text = "This is a verified AI response."
    mock_genai_client.models.generate_content.return_value = mock_response

    service = GoogleGeminiService(api_key="TEST_KEY")
    service.client = mock_genai_client

    context = OrchestrationContext(
        url="http://test.com/cache",
        schema={"title": "string"},
        ai_prompt="Summarize"
    )
    context.verification_result = {"passed": True}
    context.extracted_data = {"title": "Test Item"}

    result = service.generate_answer(context)
    
    assert result.ai_answer == "This is a verified AI response."
    assert result.metadata["ai_cached_fallback"] is False
    
    # Verify cache got populated
    cache_key = service.cache.generate_key(context.url, context.ai_prompt, context.schema)
    assert service.cache.get(cache_key) == "This is a verified AI response."


def test_gemini_service_cache_hit_avoids_api_call(mock_genai_client):
    """Verify that a cache hit bypasses the Gemini API entirely and handles failures implicitly."""
    service = GoogleGeminiService(api_key="TEST_KEY")
    service.client = mock_genai_client
    
    context = OrchestrationContext(
        url="http://test.com/hit",
        schema={"title": "string"},
        ai_prompt="Summarize"
    )
    context.verification_result = {"passed": True}
    context.extracted_data = {"title": "Test Item"}
    
    cache_key = service.cache.generate_key(context.url, context.ai_prompt, context.schema)
    service.cache.set(cache_key, "Cached fallback answer.")

    result = service.generate_answer(context)
    
    assert result.ai_answer == "Cached fallback answer."
    assert result.metadata["ai_cached_fallback"] is True
    
    # Client should not have been called
    mock_genai_client.models.generate_content.assert_not_called()


def test_gemini_service_never_caches_unverified_data():
    """Verify Trust Gate prevents caching of unverified data."""
    service = GoogleGeminiService(api_key="TEST_KEY")
    context = OrchestrationContext(
        url="http://test.com/unverified",
        schema={"title": "string"},
        ai_prompt="Summarize"
    )
    context.verification_result = {"passed": False} # Unverified
    
    with pytest.raises(RuntimeError):
        service.generate_answer(context)
        
    cache_key = service.cache.generate_key(context.url, context.ai_prompt, context.schema)
    assert service.cache.get(cache_key) is None
