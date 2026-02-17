#!/usr/bin/env python3
"""
Shared LLM utility for SkillScale skills.

Provides a unified `chat()` function that works with all configured
providers (Azure OpenAI, SiliconFlow/OpenAI-compat, Zhipu AI).
Reads configuration from the project .env file.

Usage in a skill script:
    from llm_utils import chat
    result = chat("You are a helpful assistant.", "Summarize this text: ...")
"""

import os
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Load .env from the project root (two levels up from skills/)
# ---------------------------------------------------------------------------
_PROJECT_ROOT = Path(__file__).resolve().parent.parent

try:
    from dotenv import load_dotenv
    load_dotenv(_PROJECT_ROOT / ".env")
except ImportError:
    # Fall back to manual parsing if python-dotenv is not installed
    _env_path = _PROJECT_ROOT / ".env"
    if _env_path.exists():
        for line in _env_path.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, _, val = line.partition("=")
                os.environ.setdefault(key.strip(), val.strip())

# ---------------------------------------------------------------------------
# Provider configuration
# ---------------------------------------------------------------------------
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "azure").lower()

# Azure OpenAI
AZURE_API_KEY = os.getenv("AZURE_API_KEY", "")
AZURE_API_BASE = os.getenv("AZURE_API_BASE", "")
AZURE_API_VERSION = os.getenv("AZURE_API_VERSION", "2024-12-01-preview")
AZURE_MODEL = os.getenv("AZURE_MODEL", "gpt-4o")

# OpenAI-compatible (SiliconFlow / DeepSeek)
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_API_BASE = os.getenv("OPENAI_API_BASE", "https://api.openai.com/v1")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o")

# Zhipu AI (GLM)
ZHIPU_API_KEY = os.getenv("ZHIPU_API_KEY", "")
ZHIPU_MODEL = os.getenv("ZHIPU_MODEL", "GLM-4.7-FlashX")
ZHIPU_API_BASE = "https://open.bigmodel.cn/api/paas/v4"


def _build_client():
    """Build an OpenAI-compatible client for the active provider."""
    from openai import OpenAI, AzureOpenAI

    if LLM_PROVIDER == "azure":
        if not AZURE_API_KEY:
            raise RuntimeError("AZURE_API_KEY not set in .env")
        return AzureOpenAI(
            api_key=AZURE_API_KEY,
            api_version=AZURE_API_VERSION,
            azure_endpoint=AZURE_API_BASE,
        ), AZURE_MODEL

    elif LLM_PROVIDER == "openai":
        if not OPENAI_API_KEY:
            raise RuntimeError("OPENAI_API_KEY not set in .env")
        return OpenAI(
            api_key=OPENAI_API_KEY,
            base_url=OPENAI_API_BASE,
        ), OPENAI_MODEL

    elif LLM_PROVIDER == "zhipu":
        if not ZHIPU_API_KEY:
            raise RuntimeError("ZHIPU_API_KEY not set in .env")
        return OpenAI(
            api_key=ZHIPU_API_KEY,
            base_url=ZHIPU_API_BASE,
        ), ZHIPU_MODEL

    else:
        raise RuntimeError(f"Unknown LLM_PROVIDER: {LLM_PROVIDER}")


# Lazy singleton
_client = None
_model = None


def _get_client():
    global _client, _model
    if _client is None:
        _client, _model = _build_client()
    return _client, _model


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def chat(
    system_prompt: str,
    user_message: str,
    *,
    max_tokens: int = 101240,
    temperature: float = 0.3,
) -> str:
    """
    Send a chat completion request and return the assistant's reply.

    Args:
        system_prompt: System-level instructions for the LLM.
        user_message:  The user's input / data to process.
        max_tokens:    Maximum tokens in the response.
        temperature:   Sampling temperature (lower = more deterministic).

    Returns:
        The assistant's response text.
    """
    client, model = _get_client()
    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message},
        ],
        max_tokens=max_tokens,
        temperature=temperature,
    )
    return response.choices[0].message.content.strip()


def chat_with_messages(
    messages: list[dict],
    *,
    max_tokens: int = 101240,
    temperature: float = 0.3,
) -> str:
    """
    Send a multi-turn chat completion request.

    Args:
        messages: List of {"role": ..., "content": ...} dicts.
        max_tokens: Maximum tokens in the response.
        temperature: Sampling temperature.

    Returns:
        The assistant's response text.
    """
    client, model = _get_client()
    response = client.chat.completions.create(
        model=model,
        messages=messages,
        max_tokens=max_tokens,
        temperature=temperature,
    )
    return response.choices[0].message.content.strip()


def get_provider_info() -> dict:
    """Return current provider configuration (for diagnostics)."""
    _, model = _get_client()
    return {
        "provider": LLM_PROVIDER,
        "model": model,
    }


# ---------------------------------------------------------------------------
# Quick self-test
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    info = get_provider_info()
    print(f"Provider: {info['provider']}")
    print(f"Model:    {info['model']}")
    result = chat("You are a helpful assistant.", "Say hello in exactly 5 words.")
    print(f"Response: {result}")
