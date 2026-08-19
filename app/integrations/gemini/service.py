"""Google Gemini AI Service integration for generating field-referenced answers."""

import json
import logging
import os
from typing import Optional

from google import genai
from google.genai import errors

from app.integrations.gemini.cache import AICache
from app.orchestrator.context import OrchestrationContext
from app.orchestrator.interfaces import AIService

logger = logging.getLogger(__name__)


class GoogleGeminiService(AIService):
    """Google Gemini LLM Service for processing SentinelScrape verified data."""

    def __init__(
        self, 
        api_key: Optional[str] = None, 
        model_name: str = "gemini-2.5-flash",
        cache: Optional[AICache] = None
    ):
        self.api_key = api_key or os.environ.get("GEMINI_API_KEY")
        self.model_name = model_name
        self.client = genai.Client(api_key=self.api_key) if self.api_key else None
        self.cache = cache or AICache()

    def _build_prompt(self, context: OrchestrationContext, user_prompt: Optional[str]) -> str:
        """Constructs the strict, field-referenced prompt for Gemini."""
        schema_str = json.dumps(context.schema, indent=2) if context.schema else "No schema provided."
        data_str = json.dumps(context.extracted_data, indent=2) if context.extracted_data else "No data extracted."
        question = user_prompt or context.ai_prompt or "Analyze the provided extracted data."

        return f"""You are a precise analytical assistant. Your task is to answer the user's question based STRICTLY on the provided Verified Extracted Data.

## Verified Extracted Data
{data_str}

## Schema
{schema_str}

## User Question
{question}

## Instructions
1. You MUST provide field-referenced answers. Whenever you make a claim, explicitly mention which field key in the Verified Extracted Data supports your claim.
2. If the data does not contain the answer, you MUST state that it is not available. 
3. DO NOT invent, infer, or hallucinate missing values.
4. Base your entire response ONLY on the provided Verified Extracted Data."""

    def generate_answer(self, context: OrchestrationContext, prompt: Optional[str] = None) -> OrchestrationContext:
        """Calls the Gemini API to answer the user's prompt using the verified data."""
        # 1. Invariant: Never process unverified data
        if not context.is_verified():
            raise RuntimeError("Security violation: Attempted AI generation on unverified data.")

        # 2. Check Cache
        resolved_prompt = prompt or context.ai_prompt
        cache_key = self.cache.generate_key(context.url, resolved_prompt, context.schema)
        cached_answer = self.cache.get(cache_key)

        if cached_answer:
            logger.info("Serving AI answer from cache.")
            context.ai_answer = cached_answer
            context.metadata["ai_cached_fallback"] = True
            return context

        # 3. Prevent API calls if client is not configured
        if not self.client:
            logger.error("Gemini client is not configured (missing GEMINI_API_KEY).")
            context.metadata["error"] = "Gemini API key is not configured."
            context.ai_answer = "Error: Gemini AI service is unavailable due to missing configuration."
            context.metadata["ai_cached_fallback"] = False
            return context

        # 4. Build structured prompt
        full_prompt = self._build_prompt(context, resolved_prompt)

        # 5. Generate content
        try:
            response = self.client.models.generate_content(
                model=self.model_name,
                contents=full_prompt,
            )
            context.ai_answer = response.text
            context.metadata["ai_cached_fallback"] = False
            
            # Store in cache on success
            self.cache.set(cache_key, response.text)
        except errors.APIError as e:
            logger.error("Gemini APIError encountered: %s", str(e))
            context.metadata["error"] = f"Gemini APIError: {str(e)}"
            context.ai_answer = "Error: Failed to generate AI answer due to an API error."
            context.metadata["ai_cached_fallback"] = False
            raise e
        except Exception as e:
            logger.exception("Unexpected error during Gemini generation: %s", e)
            context.metadata["error"] = f"Unexpected Gemini error: {str(e)}"
            context.ai_answer = "Error: Unexpected failure during AI generation."
            context.metadata["ai_cached_fallback"] = False
            raise e

        return context
