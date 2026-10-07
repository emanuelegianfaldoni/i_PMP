import os
import json
import time
import requests

_token_cache: dict = {"token": None, "expires_at": 0.0}


def _get_token() -> str:
    now = time.time()
    if _token_cache["token"] and now < _token_cache["expires_at"]:
        return _token_cache["token"]

    auth_url = os.getenv("AICORE_AUTH_URL", "").rstrip("/") + "/oauth/token"
    r = requests.post(
        auth_url,
        data={"grant_type": "client_credentials"},
        auth=(os.getenv("AICORE_CLIENT_ID"), os.getenv("AICORE_CLIENT_SECRET")),
        timeout=15,
    )
    r.raise_for_status()
    data = r.json()
    _token_cache["token"] = data["access_token"]
    _token_cache["expires_at"] = now + data.get("expires_in", 3600) - 60
    return _token_cache["token"]


def _headers() -> dict:
    return {
        "Authorization": f"Bearer {_get_token()}",
        "AI-Resource-Group": os.getenv("AICORE_RESOURCE_GROUP", "i-pmp"),
        "Content-Type": "application/json",
    }


def chat_completion(messages: list[dict], system: str = "", temperature: float = 0.2) -> str:
    deployment_id = os.getenv("CHAT_DEPLOYMENT_ID", "")
    if not deployment_id or deployment_id == "<da-compilare>":
        raise EnvironmentError(
            "CHAT_DEPLOYMENT_ID non configurato. Compilalo nel .env dopo aver deployato il modello su AI Launchpad."
        )

    base_url = os.getenv("AICORE_BASE_URL", "").rstrip("/")
    url = f"{base_url}/v2/inference/deployments/{deployment_id}/invoke"

    payload: dict = {
        "anthropic_version": "bedrock-2023-05-31",
        "max_tokens": 4096,
        "messages": messages,
        "temperature": temperature,
    }
    if system:
        payload["system"] = system

    r = requests.post(url, headers=_headers(), json=payload, timeout=60)
    if not r.ok:
        raise requests.HTTPError(f"{r.status_code}: {r.text}", response=r)
    return r.json()["content"][0]["text"]


def get_embedding(text: str) -> list[float]:
    deployment_id = os.getenv("EMBED_DEPLOYMENT_ID", "")
    if not deployment_id or deployment_id == "<da-compilare>":
        raise EnvironmentError(
            "EMBED_DEPLOYMENT_ID non configurato. Compilalo nel .env dopo aver deployato il modello su AI Launchpad."
        )

    base_url = os.getenv("AICORE_BASE_URL", "").rstrip("/")
    url = f"{base_url}/v2/inference/deployments/{deployment_id}/v1/embeddings"

    payload = {"input": text}

    r = requests.post(url, headers=_headers(), json=payload, timeout=30)
    if not r.ok:
        raise requests.HTTPError(f"{r.status_code}: {r.text}", response=r)
    return r.json()["data"][0]["embedding"]
