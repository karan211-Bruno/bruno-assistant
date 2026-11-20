"""Configuration and secret-safe settings management."""

from __future__ import annotations

import json
import os
from typing import Any

from backend.db import PROJECT_ROOT, db_session, now_iso

SECRET_KEYS = {
    "gemini_api_key",
    "openai_api_key",
    "anthropic_api_key",
    "vapi_api_key",
    "retell_api_key",
    "twilio_auth_token",
}

BOOL_KEYS = {"require_call_approval", "auto_speak"}

DEFAULTS: dict[str, Any] = {
    "llm_provider": "auto",
    "gemini_api_key": "",
    "gemini_model": "gemini-2.5-flash",
    "openai_api_key": "",
    "openai_model": "gpt-4.1-mini",
    "anthropic_api_key": "",
    "anthropic_model": "claude-sonnet-4-20250514",
    "ollama_base_url": "http://127.0.0.1:11434",
    "ollama_model": "qwen2.5:7b",
    "telephony_provider": "simulation",
    "vapi_api_key": "",
    "vapi_phone_number_id": "",
    "retell_api_key": "",
    "retell_from_number": "",
    "retell_agent_id": "",
    "twilio_account_sid": "",
    "twilio_auth_token": "",
    "twilio_from_number": "",
    "public_base_url": "https://bruno-sony.tail617e7a.ts.net",
    "user_name": "브루노",
    "user_phone": "",
    "user_timezone": "Asia/Seoul",
    "require_call_approval": True,
    "auto_speak": False,
}

ENV_MAP: dict[str, str] = {
    "llm_provider": "LLM_PROVIDER",
    "gemini_api_key": "GEMINI_API_KEY",
    "gemini_model": "GEMINI_MODEL",
    "openai_api_key": "OPENAI_API_KEY",
    "openai_model": "OPENAI_MODEL",
    "anthropic_api_key": "ANTHROPIC_API_KEY",
    "anthropic_model": "ANTHROPIC_MODEL",
    "ollama_base_url": "OLLAMA_BASE_URL",
    "ollama_model": "OLLAMA_MODEL",
    "telephony_provider": "TELEPHONY_PROVIDER",
    "vapi_api_key": "VAPI_API_KEY",
    "vapi_phone_number_id": "VAPI_PHONE_NUMBER_ID",
    "retell_api_key": "RETELL_API_KEY",
    "retell_from_number": "RETELL_FROM_NUMBER",
    "retell_agent_id": "RETELL_AGENT_ID",
    "twilio_account_sid": "TWILIO_ACCOUNT_SID",
    "twilio_auth_token": "TWILIO_AUTH_TOKEN",
    "twilio_from_number": "TWILIO_FROM_NUMBER",
    "public_base_url": "PUBLIC_BASE_URL",
    "user_name": "BRUNO_USER_NAME",
    "user_phone": "BRUNO_USER_PHONE",
    "user_timezone": "BRUNO_USER_TIMEZONE",
}


def _load_dotenv_file() -> dict[str, str]:
    """Read key=value pairs from .env if present (never raises)."""
    env_path = PROJECT_ROOT / ".env"
    if not env_path.is_file():
        return {}
    try:
        from dotenv import dotenv_values

        raw = dotenv_values(env_path)
        return {k: str(v) for k, v in raw.items() if k and v is not None}
    except Exception:
        return {}


def _coerce_bool(val: Any, default: bool) -> bool:
    if isinstance(val, bool):
        return val
    if isinstance(val, (int, float)):
        return bool(val)
    if isinstance(val, str):
        low = val.strip().lower()
        if low in {"1", "true", "yes", "on"}:
            return True
        if low in {"0", "false", "no", "off"}:
            return False
    return default


def mask_secret(value: str) -> str:
    """Return a masked representation of a secret string."""
    s = (value or "").strip()
    if not s:
        return ""
    if len(s) <= 4:
        return "****"
    return "****" + s[-4:]


def get_settings(include_secrets: bool = False) -> dict[str, Any]:
    """Load merged settings from defaults -> .env -> environment -> SQLite settings."""
    merged: dict[str, Any] = dict(DEFAULTS)
    dotenv_vals = _load_dotenv_file()

    for key, env_name in ENV_MAP.items():
        env_val = os.environ.get(env_name)
        if env_val is None:
            env_val = dotenv_vals.get(env_name)
        if env_val is not None and env_val != "":
            merged[key] = _coerce_bool(env_val, bool(DEFAULTS[key])) if key in BOOL_KEYS else env_val

    with db_session() as conn:
        rows = conn.execute("SELECT key, value FROM settings").fetchall()
        for row in rows:
            k = row["key"]
            if k not in DEFAULTS:
                continue
            try:
                val = json.loads(row["value"])
            except Exception:
                val = row["value"]
            if k in BOOL_KEYS:
                merged[k] = _coerce_bool(val, bool(DEFAULTS[k]))
            elif k in SECRET_KEYS:
                if isinstance(val, str) and val.strip():
                    merged[k] = val.strip()
            elif val is not None:
                merged[k] = val

    if include_secrets:
        return merged

    safe: dict[str, Any] = {}
    for k, v in merged.items():
        if k in SECRET_KEYS:
            secret_str = str(v or "")
            safe[k] = mask_secret(secret_str)
            safe[f"has_{k}"] = bool(secret_str.strip())
        else:
            safe[k] = v
    return safe


def save_settings(updates: dict[str, Any]) -> dict[str, Any]:
    """Persist settings updates while preserving existing secrets unless explicitly changed or cleared."""
    ts = now_iso()
    with db_session() as conn:
        for secret_key in SECRET_KEYS:
            if updates.get(f"clear_{secret_key}") is True:
                conn.execute("DELETE FROM settings WHERE key = ?", (secret_key,))

        for key, value in updates.items():
            if key not in DEFAULTS:
                continue
            if key in SECRET_KEYS:
                if value is None:
                    continue
                sval = str(value).strip()
                if not sval or sval.startswith("****"):
                    continue
                stored = json.dumps(sval)
            elif key in BOOL_KEYS:
                stored = json.dumps(_coerce_bool(value, bool(DEFAULTS[key])))
            else:
                stored = json.dumps(str(value).strip() if value is not None else "")

            conn.execute(
                """
                INSERT INTO settings (key, value, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at
                """,
                (key, stored, ts),
            )

    return get_settings(include_secrets=False)
