"""Tiny LLM client with no third-party dependencies.

ollama  -> native /api/chat with JSON-schema constrained output (best on the Jetson)
openai  -> any OpenAI-compatible /v1/chat/completions (llama.cpp, vLLM, Groq, OpenRouter, ...)
"""
import json
import urllib.error
import urllib.request

import config


class LLMError(RuntimeError):
    pass


def _post(url, payload, headers=None, timeout=None):
    req = urllib.request.Request(url, data=json.dumps(payload).encode(), method="POST",
                                 headers={"Content-Type": "application/json", **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=timeout or config.LLM_TIMEOUT) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        raise LLMError(f"{e.code} from {url}: {e.read()[:400].decode(errors='ignore')}") from e
    except urllib.error.URLError as e:
        raise LLMError(f"cannot reach {url}: {e.reason}") from e


def chat(messages, schema=None, temperature=0.2, model=None):
    model = model or config.LLM_MODEL
    if config.LLM_BACKEND == "ollama":
        payload = {"model": model, "messages": messages, "stream": False,
                   "options": {"temperature": temperature, "num_ctx": config.LLM_NUM_CTX}}
        if schema:
            payload["format"] = schema
        out = _post(f"{config.LLM_URL.rstrip('/')}/api/chat", payload)
        return out["message"]["content"]

    payload = {"model": model, "messages": messages, "temperature": temperature}
    if schema:
        payload["response_format"] = {"type": "json_object"}
        messages = messages[:-1] + [{**messages[-1], "content": messages[-1]["content"]
                                     + "\n\nReturn ONLY JSON matching this schema:\n" + json.dumps(schema)}]
        payload["messages"] = messages
    headers = {"Authorization": f"Bearer {config.LLM_API_KEY}"} if config.LLM_API_KEY else {}
    out = _post(f"{config.LLM_URL.rstrip('/')}/v1/chat/completions", payload, headers)
    return out["choices"][0]["message"]["content"]


def chat_json(messages, schema, **kw):
    text = chat(messages, schema=schema, **kw).strip()
    if text.startswith("```"):
        text = text.strip("`").removeprefix("json").strip()
    try:
        return json.loads(text)
    except ValueError:
        start, end = text.find("{"), text.rfind("}")
        if start != -1 and end > start:
            return json.loads(text[start:end + 1])
        raise LLMError(f"model did not return JSON: {text[:300]}")


def status():
    """Cheap reachability check for the dashboard's model pill."""
    try:
        if config.LLM_BACKEND == "ollama":
            with urllib.request.urlopen(f"{config.LLM_URL.rstrip('/')}/api/tags", timeout=3) as r:
                names = [m["name"] for m in json.loads(r.read()).get("models", [])]
            return {"ok": config.LLM_MODEL in names or f"{config.LLM_MODEL}:latest" in names,
                    "backend": "ollama", "model": config.LLM_MODEL, "url": config.LLM_URL,
                    "available": names}
        return {"ok": True, "backend": config.LLM_BACKEND, "model": config.LLM_MODEL, "url": config.LLM_URL}
    except Exception as e:
        return {"ok": False, "backend": config.LLM_BACKEND, "model": config.LLM_MODEL,
                "url": config.LLM_URL, "error": str(e)}
