"""Small, provider-neutral adapter for OpenAI-compatible chat routers."""
import base64
import json
from io import BytesIO
from urllib.parse import urlsplit
import httpx
from PIL import Image, ImageOps
from app.core.config import get_settings
from app.core.errors import DomainError

PROMPT_VERSION = "branchframe-router-v1"

def configured():
    s = get_settings()
    try:
        url = urlsplit(s.ai_base_url)
        url.port  # Validate malformed ports without exposing configuration.
    except ValueError:
        return False
    return bool((s.ai_api_key or s.openai_api_key) and (s.ai_model or s.openai_model)
        and url.scheme in ("http", "https") and url.hostname
        and not url.username and not url.password and not url.query and not url.fragment)

def require_configured():
    if not configured():
        raise DomainError(422, "AI_NOT_CONFIGURED", "Configure AI_BASE_URL, AI_API_KEY and AI_MODEL on the server.")

def complete(messages, *, structured=True, max_tokens=None):
    require_configured()
    s = get_settings()
    body = {"model": s.ai_model or s.openai_model, "messages": messages, "stream": False,
        "max_tokens": max_tokens or s.ai_max_output_tokens}
    if structured and s.ai_json_mode:
        body["response_format"] = {"type": "json_object"}
    try:
        response = httpx.post(s.ai_base_url.rstrip("/") + "/chat/completions",
            headers={"Authorization": "Bearer " + (s.ai_api_key or s.openai_api_key),
                "Content-Type": "application/json"},
            json=body, timeout=s.ai_timeout_seconds, follow_redirects=False)
        response.raise_for_status()
        if len(response.content) > 2 * 1024 * 1024:
            raise ValueError("Response exceeds limit")
        payload = response.json()
        choice = payload["choices"][0]
        if choice.get("finish_reason") in ("length", "content_filter"):
            raise ValueError("Incomplete or blocked completion")
        content = choice["message"]["content"]
        if not isinstance(content, str) or not content.strip():
            raise ValueError("Empty completion")
        return content
    except httpx.TimeoutException:
        raise DomainError(504, "PROVIDER_TIMEOUT", "The AI router request timed out.") from None
    except Exception:
        raise DomainError(502, "PROVIDER_ERROR",
            "The AI router request failed. Check credentials, model support and router availability.") from None

def complete_json(messages):
    content = complete(messages).strip()
    try:
        if content.startswith("```") and content.endswith("```"):
            content = content.split("\n", 1)[1].rsplit("```", 1)[0].strip()
        result = json.loads(content)
        if not isinstance(result, dict):
            raise ValueError("Expected object")
        return result
    except (ValueError, TypeError, IndexError):
        raise DomainError(502, "PROVIDER_ERROR", "The AI router returned invalid structured output.") from None

def image_data_url(path):
    # Only prevalidated local paths are accepted; never download arbitrary public URLs.
    with Image.open(path) as source:
        image = ImageOps.exif_transpose(source).convert("RGB")
        image.thumbnail((1024, 1024))
        out = BytesIO()
        image.save(out, format="JPEG", quality=85)
    return "data:image/jpeg;base64," + base64.b64encode(out.getvalue()).decode("ascii")
