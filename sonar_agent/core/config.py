"""
Configuration management for the SonarQube Code Correction Agent.

Loads settings from .env file and validates required values.
"""

import os
import sys
from pathlib import Path
from dotenv import load_dotenv


def _load_env() -> None:
    """Load .env file from the project root."""
    env_path = Path(__file__).resolve().parent.parent.parent / ".env"
    if env_path.exists():
        load_dotenv(env_path)
    else:
        print(
            "[!] No .env file found. Copy .env.example to .env and fill in your values."
        )
        sys.exit(1)


_load_env()

# ── SonarQube connection ────────────────────────────────────────────────
SONAR_HOST_URL: str = os.getenv("SONAR_HOST_URL", "http://localhost:9000").rstrip("/")
SONAR_TOKEN: str = os.getenv("SONAR_TOKEN", "")
SONAR_PROJECT_KEY: str = os.getenv("SONAR_PROJECT_KEY", "")

# ── Project paths ───────────────────────────────────────────────────────
PROJECT_PATH: str = os.getenv("PROJECT_PATH", "")
BACKUP_DIR: str = os.getenv("BACKUP_DIR", ".sonar-backups")

# ── LLM providers (at least one API key required) ───────────────────────
# Each provider has a built-in list of model variants to try.
# You only need to supply the API key — models are handled automatically.
ANTHROPIC_API_KEY: str = os.getenv("ANTHROPIC_API_KEY", "")
GEMINI_API_KEY: str = os.getenv("GEMINI_API_KEY", "")
OPENAI_API_KEY: str = os.getenv("OPENAI_API_KEY", "")
GROQ_API_KEY: str = os.getenv("GROQ_API_KEY", "")


def _has_key(val: str) -> bool:
    return bool(val) and not val.startswith("<")


def validate() -> None:
    """Ensure all required configuration values are present."""
    missing: list[str] = []
    if not SONAR_TOKEN or SONAR_TOKEN.startswith("<"):
        missing.append("SONAR_TOKEN")
    if not SONAR_PROJECT_KEY or SONAR_PROJECT_KEY.startswith("<"):
        missing.append("SONAR_PROJECT_KEY")
    if not PROJECT_PATH or PROJECT_PATH.startswith("<"):
        missing.append("PROJECT_PATH")

    # At least one LLM provider must be configured
    llm_keys = [ANTHROPIC_API_KEY, GEMINI_API_KEY, OPENAI_API_KEY, GROQ_API_KEY]
    if not any(_has_key(k) for k in llm_keys):
        missing.append("At least one LLM API key (ANTHROPIC / GEMINI / OPENAI / GROQ)")

    if missing:
        print(f"[!] Missing required config values: {', '.join(missing)}")
        print("    Edit your .env file and fill them in.")
        sys.exit(1)

    if not Path(PROJECT_PATH).is_dir():
        print(f"[!] PROJECT_PATH does not exist or is not a directory: {PROJECT_PATH}")
        sys.exit(1)
