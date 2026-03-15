"""
LLM provider implementations for the fallback chain.

Each provider wraps a different API (Gemini, OpenAI, Mistral, Groq) behind
a common interface. Every provider carries an ordered list of model variants
to try — if a specific model hits its rate limit, the provider tries the
next model before giving up entirely.

Fallback order (as of March 2026):
  Gemini (6 models) → OpenAI (7 models) → Groq (6 models)
  = 19 total model variants
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Optional

from sonar_agent.core import config


class LLMProvider(ABC):
    """Base class for all LLM providers."""

    name: str = "base"
    MODELS: list[str] = []

    def __init__(self) -> None:
        self._exhausted_models: set[str] = set()
        self.last_model_used: str | None = None

    @abstractmethod
    def is_configured(self) -> bool:
        ...

    @abstractmethod
    def _call_model(self, model: str, system_prompt: str, user_prompt: str) -> Optional[str]:
        ...

    def generate(self, system_prompt: str, user_prompt: str) -> Optional[str]:
        """
        Try each model in order until one succeeds.

        - Model-level rate limits → skip that model, try next.
        - Account-level quota → raise QuotaExhaustedError.
        """
        available = [m for m in self.MODELS if m not in self._exhausted_models]
        if not available:
            raise QuotaExhaustedError(f"{self.name}: all models exhausted")

        last_error = None
        for model in available:
            try:
                result = self._call_model(model, system_prompt, user_prompt)
                if result:
                    self.last_model_used = model
                    return result
            except QuotaExhaustedError:
                raise
            except ModelRateLimitError as exc:
                self._exhausted_models.add(model)
                last_error = exc
                continue
            except Exception as exc:
                msg = str(exc)
                if _is_quota_exhausted_error(msg):
                    raise QuotaExhaustedError(f"{self.name}: {msg}")
                elif _is_rate_limit_error(msg):
                    self._exhausted_models.add(model)
                    last_error = ModelRateLimitError(f"{self.name}/{model}: {msg}")
                    continue
                else:
                    last_error = exc
                    continue

        if last_error:
            raise QuotaExhaustedError(f"{self.name}: all models exhausted — {last_error}")
        return None

    @property
    def active_models(self) -> list[str]:
        return [m for m in self.MODELS if m not in self._exhausted_models]

    def _has_key(self, key: str) -> bool:
        return bool(key) and not key.startswith("<")


class QuotaExhaustedError(Exception):
    """Raised when a provider's quota / rate limit is hit."""
    pass


class ModelRateLimitError(Exception):
    """Raised when a specific model (not the whole account) is rate-limited."""
    pass


def _is_quota_exhausted_error(error_msg: str) -> bool:
    """Check if an error message indicates an account-level quota/billing exhaustion."""
    keywords = [
        "quota", "insufficient_quota", "resource exhausted", "resourceexhausted", "billing"
    ]
    msg = error_msg.lower()
    return any(kw in msg for kw in keywords)

def _is_rate_limit_error(error_msg: str) -> bool:
    """Check if an error message indicates a model-level rate limit."""
    keywords = [
        "rate limit", "rate_limit", "429", "too many requests",
        "tokens per minute", "requests per minute"
    ]
    msg = error_msg.lower()
    return any(kw in msg for kw in keywords)


# ═══════════════════════════════════════════════════════════════════════════
#  Gemini — 6 models (free tier via Google AI Studio)
# ═══════════════════════════════════════════════════════════════════════════

class GeminiProvider(LLMProvider):
    name = "Gemini"
    MODELS = [
        "gemini-2.5-flash-preview-05-20",   # Latest 2.5 Flash preview
        "gemini-2.5-pro-preview-05-06",      # Latest 2.5 Pro preview
        "gemini-2.0-flash",                  # Stable 2.0 Flash
        "gemini-2.0-flash-lite",             # Lightweight 2.0 Flash
        "gemini-3-flash",                    # Gemini 3 Flash (Dec 2025)
        "gemini-3.1-flash-lite",             # Gemini 3.1 Flash Lite (Mar 2026)
    ]

    def is_configured(self) -> bool:
        return self._has_key(config.GEMINI_API_KEY)

    def _call_model(self, model: str, system_prompt: str, user_prompt: str) -> Optional[str]:
        from google import genai
        from google.genai import types

        client = genai.Client(api_key=config.GEMINI_API_KEY)
        response = client.models.generate_content(
            model=model,
            contents=user_prompt,
            config=types.GenerateContentConfig(
                system_instruction=system_prompt,
                temperature=0.0,
                max_output_tokens=8192,
            ),
        )
        return response.text if response.text else None


# ═══════════════════════════════════════════════════════════════════════════
#  OpenAI — 7 models
# ═══════════════════════════════════════════════════════════════════════════

class OpenAIProvider(LLMProvider):
    name = "OpenAI"
    MODELS = [
        "gpt-4o-mini",              # Cheapest GPT-4 class
        "gpt-4o",                   # Full GPT-4o multimodal
        "gpt-4.1-mini",             # GPT-4.1 mini (strong instruction following)
        "gpt-4.1-nano",             # GPT-4.1 nano (fastest, cheapest)
        "gpt-4.1",                  # GPT-4.1 (long context, instruction following)
        "o4-mini",                  # o4-mini reasoning model
        "o3-mini",                  # o3-mini reasoning model
    ]

    def is_configured(self) -> bool:
        return self._has_key(config.OPENAI_API_KEY)

    def _call_model(self, model: str, system_prompt: str, user_prompt: str) -> Optional[str]:
        from openai import OpenAI

        client = OpenAI(api_key=config.OPENAI_API_KEY)
        response = client.chat.completions.create(
            model=model,
            temperature=0.0,
            max_tokens=8192,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        )
        content = response.choices[0].message.content
        return content if content else None



# ═══════════════════════════════════════════════════════════════════════════
#  Groq — 6 models (generous free tier on LPU hardware)
# ═══════════════════════════════════════════════════════════════════════════

class GroqProvider(LLMProvider):
    name = "Groq"
    MODELS = [
        "llama-3.3-70b-versatile",      # Llama 3.3 70B — best quality
        "llama-3.1-8b-instant",          # Llama 3.1 8B — fastest
        "openai/gpt-oss-120b",           # OpenAI GPT-OSS 120B — open weights
        "openai/gpt-oss-20b",            # OpenAI GPT-OSS 20B — fast open weights
        "qwen-qwq-32b",                 # Qwen QWQ 32B — reasoning
        "deepseek-r1-distill-llama-70b", # DeepSeek R1 distilled — reasoning
    ]

    def is_configured(self) -> bool:
        return self._has_key(config.GROQ_API_KEY)

    def _call_model(self, model: str, system_prompt: str, user_prompt: str) -> Optional[str]:
        from groq import Groq

        client = Groq(api_key=config.GROQ_API_KEY)
        response = client.chat.completions.create(
            model=model,
            temperature=0.0,
            max_tokens=8192,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        )
        content = response.choices[0].message.content
        return content if content else None


# ═══════════════════════════════════════════════════════════════════════════
#  Provider registry — order defines fallback priority
# ═══════════════════════════════════════════════════════════════════════════

ALL_PROVIDERS: list[type[LLMProvider]] = [
    GroqProvider,
    GeminiProvider,
    OpenAIProvider,
]

# Total: 19 model variants across 3 providers
