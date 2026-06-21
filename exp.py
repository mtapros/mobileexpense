import argparse
import json
import os
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from typing import Any

try:
    from fastapi import FastAPI, HTTPException
    from fastapi.responses import HTMLResponse
    from pydantic import BaseModel, Field
except ImportError as exc:
    raise RuntimeError("Install dependencies with: pip install fastapi uvicorn") from exc


DEFAULT_LM_STUDIO_HOST = os.environ.get("LMSTUDIO_HOST", "192.168.34.82")
DEFAULT_LM_STUDIO_PORT = int(os.environ.get("LMSTUDIO_PORT", "1234"))
DEFAULT_LM_STUDIO_ENDPOINT = os.environ.get(
    "LMSTUDIO_ENDPOINT",
    f"http://{DEFAULT_LM_STUDIO_HOST}:{DEFAULT_LM_STUDIO_PORT}/v1/chat/completions",
)
DEFAULT_MODEL = os.environ.get("LMSTUDIO_MODEL", "Qwen/Qwen3.6-35B-A3B")
DEFAULT_API_HOST = os.environ.get("LM_CHAT_API_HOST", "0.0.0.0")
DEFAULT_API_PORT = int(os.environ.get("LM_CHAT_API_PORT", "8001"))
DEFAULT_TIMEOUT_SECONDS = int(os.environ.get("LMSTUDIO_TIMEOUT_SECONDS", "120"))
MAX_QUERY_CHARS = 20000


def derive_models_endpoint(chat_endpoint_url: str) -> str:
    """Derive LM Studio's OpenAI-compatible /v1/models URL from the chat URL."""
    explicit_endpoint = os.environ.get("LMSTUDIO_MODELS_ENDPOINT", "").strip()
    if explicit_endpoint:
        return explicit_endpoint

    parsed = urllib.parse.urlsplit(chat_endpoint_url.strip())
    if not parsed.scheme or not parsed.netloc:
        return chat_endpoint_url.strip()

    path = parsed.path.rstrip("/")
    if path.endswith("/v1/chat/completions"):
        path = path[: -len("/chat/completions")] + "/models"
    elif path.endswith("/chat/completions"):
        path = path[: -len("/chat/completions")] + "/models"
    elif path.endswith("/v1"):
        path = path + "/models"
    else:
        path = "/v1/models"

    return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, path, "", ""))


class ChatRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=MAX_QUERY_CHARS)
    system_prompt: str = Field(default="", max_length=4000)
    model: str = Field(default="", max_length=300)
    temperature: float = Field(default=0.7, ge=0.0, le=2.0)
    max_tokens: int | None = Field(default=None, ge=1, le=32768)


class LmStudioClientError(Exception):
    pass


class LmStudioClient:
    def __init__(
        self,
        endpoint_url: str,
        default_model: str,
        api_key: str = "",
        timeout_seconds: int = 120,
        models_endpoint_url: str = "",
    ) -> None:
        self.endpoint_url = endpoint_url.strip()
        self.default_model = default_model.strip()
        self.api_key = api_key.strip()
        self.timeout_seconds = max(5, int(timeout_seconds))
        self.models_endpoint_url = (models_endpoint_url or derive_models_endpoint(self.endpoint_url)).strip()

    def chat(self, request: ChatRequest) -> dict[str, Any]:
        if not self.endpoint_url:
            raise LmStudioClientError("LM Studio endpoint URL is required.")
        model = (request.model or self.default_model).strip()
        if not model:
            raise LmStudioClientError("LM Studio model is required.")

        messages: list[dict[str, str]] = []
        system_prompt = request.system_prompt.strip()
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": request.query.strip()})

        payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": request.temperature,
            "stream": False,
        }
        if request.max_tokens is not None:
            payload["max_tokens"] = request.max_tokens

        raw_response = self._request_json(
            self.endpoint_url,
            method="POST",
            body=json.dumps(payload).encode("utf-8"),
        )
        answer = self._extract_answer(raw_response)
        return {
            "ok": True,
            "model": model,
            "answer": answer,
            "usage": raw_response.get("usage") if isinstance(raw_response, dict) else None,
            "raw": raw_response,
            "created_at": datetime.now().isoformat(timespec="seconds"),
        }

    def models(self) -> dict[str, Any]:
        if not self.models_endpoint_url:
            raise LmStudioClientError("LM Studio models endpoint URL is required.")

        raw_response = self._request_json(self.models_endpoint_url, method="GET")
        models = self._extract_model_ids(raw_response)
        return {
            "ok": True,
            "models": models,
            "count": len(models),
            "default_model": self.default_model,
            "lm_studio_models_endpoint": self.models_endpoint_url,
            "raw": raw_response,
            "created_at": datetime.now().isoformat(timespec="seconds"),
        }

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if self.api_key:
            headers["Authorization"] = "Bearer " + self.api_key
        return headers

    def _request_json(self, url: str, method: str, body: bytes | None = None) -> dict[str, Any]:
        http_request = urllib.request.Request(
            url,
            data=body,
            headers=self._headers(),
            method=method,
        )
        try:
            with urllib.request.urlopen(http_request, timeout=self.timeout_seconds) as response:
                raw_text = response.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as error:
            response_body = error.read().decode("utf-8", errors="replace")
            raise LmStudioClientError(f"LM Studio returned HTTP {error.code}: {response_body or error.reason}") from error
        except urllib.error.URLError as error:
            raise LmStudioClientError(f"Could not reach LM Studio: {error.reason}") from error

        try:
            parsed = json.loads(raw_text)
        except json.JSONDecodeError as error:
            raise LmStudioClientError(f"LM Studio did not return JSON: {raw_text[:500]}") from error
        if not isinstance(parsed, dict):
            raise LmStudioClientError(f"LM Studio returned unexpected JSON: {raw_text[:500]}")
        return parsed

    @staticmethod
    def _extract_model_ids(raw_response: dict[str, Any]) -> list[str]:
        data = raw_response.get("data", []) if isinstance(raw_response, dict) else []
        if not isinstance(data, list):
            return []

        models: list[str] = []
        for item in data:
            if isinstance(item, dict):
                model_id = item.get("id")
                if model_id:
                    models.append(str(model_id))
            elif item:
                models.append(str(item))
        return models

    @staticmethod
    def _extract_answer(raw_response: dict[str, Any]) -> str:
        choices = raw_response.get("choices", []) if isinstance(raw_response, dict) else []
        if not choices:
            return ""
        first = choices[0]
        if not isinstance(first, dict):
            return ""
        message = first.get("message")
        if isinstance(message, dict):
            content = message.get("content")
            if content is not None:
                return str(content)
        text = first.get("text")
        return "" if text is None else str(text)


client = LmStudioClient(
    endpoint_url=DEFAULT_LM_STUDIO_ENDPOINT,
    default_model=DEFAULT_MODEL,
    api_key=os.environ.get("LMSTUDIO_API_KEY", ""),
    timeout_seconds=DEFAULT_TIMEOUT_SECONDS,
    models_endpoint_url=os.environ.get("LMSTUDIO_MODELS_ENDPOINT", ""),
)
app = FastAPI(title="LM Studio Chat API", version="1.1")


@app.get("/health")
async def health() -> dict[str, Any]:
    return {
        "ok": True,
        "service": "lm-studio-chat-api",
        "version": "1.1",
        "lm_studio_endpoint": client.endpoint_url,
        "lm_studio_models_endpoint": client.models_endpoint_url,
        "default_model": client.default_model,
        "server_time": datetime.now().isoformat(timespec="seconds"),
    }


@app.get("/models")
async def models() -> dict[str, Any]:
    try:
        return client.models()
    except LmStudioClientError as error:
        raise HTTPException(status_code=502, detail=str(error)) from error


@app.post("/chat")
async def chat(request: ChatRequest) -> dict[str, Any]:
    if not request.query.strip():
        raise HTTPException(status_code=400, detail="query is required")
    try:
        return client.chat(request)
    except LmStudioClientError as error:
        raise HTTPException(status_code=502, detail=str(error)) from error


@app.get("/", response_class=HTMLResponse)
async def index() -> str:
    return """<!doctype html>
<html>
<head>
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>LM Studio Chat API</title>
  <style>
    body { background:#101114; color:#f5f7fa; font-family:system-ui,sans-serif; margin:24px; }
    textarea,input,select,button { box-sizing:border-box; font:inherit; width:100%; margin-top:10px; padding:10px; }
    textarea,input,select { background:#181b20; border:1px solid #303642; color:#f5f7fa; }
    button { background:#2563eb; border:0; color:white; font-weight:700; }
    pre { background:#181b20; overflow:auto; padding:12px; white-space:pre-wrap; }
  </style>
</head>
<body>
  <h1>LM Studio Chat API</h1>
  <p>POST JSON to <code>/chat</code> with a <code>query</code> field, poll <code>/models</code>, or test both here.</p>
  <button id="models">Poll Models</button>
  <select id="model"><option value="">Use server default model</option></select>
  <textarea id="query" rows="6" placeholder="Ask the model..."></textarea>
  <button id="send">Send</button>
  <pre id="result"></pre>
  <script>
    document.getElementById('models').onclick = async () => {
      const result = document.getElementById('result');
      const select = document.getElementById('model');
      result.textContent = 'Polling models...';
      const response = await fetch('/models');
      const data = await response.json();
      select.innerHTML = '<option value="">Use server default model</option>';
      for (const model of (data.models || [])) {
        const option = document.createElement('option');
        option.value = model;
        option.textContent = model;
        select.appendChild(option);
      }
      result.textContent = JSON.stringify(data, null, 2);
    };
    document.getElementById('send').onclick = async () => {
      const result = document.getElementById('result');
      result.textContent = 'Sending...';
      const response = await fetch('/chat', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({
          query: document.getElementById('query').value,
          model: document.getElementById('model').value
        })
      });
      const data = await response.json();
      result.textContent = data.answer || JSON.stringify(data, null, 2);
    };
  </script>
</body>
</html>"""


def main() -> None:
    parser = argparse.ArgumentParser(description="Run a local API server that proxies text prompts to LM Studio.")
    parser.add_argument("--host", default=DEFAULT_API_HOST, help="API bind host for the phone/WireGuard client.")
    parser.add_argument("--port", default=DEFAULT_API_PORT, type=int, help="API port for the phone/WireGuard client.")
    args = parser.parse_args()

    import uvicorn

    uvicorn.run("exp:app", host=args.host, port=args.port, reload=False)


if __name__ == "__main__":
    main()
