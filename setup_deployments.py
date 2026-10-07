"""
Esegui una volta sola da BAS per creare i deployment su SAP AI Core
e ottenere i DEPLOYMENT_ID da mettere nel .env

Uso:
  python setup_deployments.py
"""
import os
import json
import time
import requests
from dotenv import load_dotenv

load_dotenv()

AUTH_URL   = os.getenv("AICORE_AUTH_URL") + "/oauth/token"
BASE_URL   = os.getenv("AICORE_BASE_URL").rstrip("/")
CLIENT_ID  = os.getenv("AICORE_CLIENT_ID")
CLIENT_SEC = os.getenv("AICORE_CLIENT_SECRET")
RG         = os.getenv("AICORE_RESOURCE_GROUP", "i-pmp")
CHAT_MODEL = os.getenv("CHAT_MODEL", "anthropic--claude-4.6-sonnet")
EMBED_MODEL = os.getenv("EMBED_MODEL", "text-embedding-3-large")


def get_token() -> str:
    r = requests.post(AUTH_URL, data={"grant_type": "client_credentials"},
                      auth=(CLIENT_ID, CLIENT_SEC), timeout=15)
    r.raise_for_status()
    return r.json()["access_token"]


def headers(token: str) -> dict:
    return {
        "Authorization": f"Bearer {token}",
        "AI-Resource-Group": RG,
        "Content-Type": "application/json",
    }


def list_deployments(token: str) -> list:
    r = requests.get(f"{BASE_URL}/v2/lm/deployments", headers=headers(token), timeout=15)
    r.raise_for_status()
    return r.json().get("resources", [])


def find_or_create_deployment(token: str, model_name: str) -> str:
    deps = list_deployments(token)

    # Cerca deployment già esistente per questo modello
    for d in deps:
        details = d.get("details", {}) or {}
        resources = details.get("resources", {}) or {}
        backend = resources.get("backendDetails", {}) or {}
        model = backend.get("model", {}) or {}
        if model.get("name", "") == model_name and d.get("status") == "RUNNING":
            print(f"  ✅ Trovato deployment RUNNING per {model_name}: {d['id']}")
            return d["id"]

    # Nessun deployment trovato — creane uno
    print(f"  ⏳ Nessun deployment attivo per {model_name}, creo configurazione...")

    config_body = {
        "name": f"ipmp-{model_name.replace('--', '-').replace('/', '-')}",
        "executableId": model_name,
        "scenarioId": "foundation-models",
        "versionId": "0.0.1",
        "parameterBindings": [],
        "inputArtifactBindings": [],
    }
    r = requests.post(f"{BASE_URL}/v2/lm/configurations",
                      headers=headers(token), json=config_body, timeout=15)
    if not r.ok:
        print(f"  ⚠️  Configurazione fallita: {r.text}")
        return ""
    config_id = r.json()["id"]
    print(f"  Config creata: {config_id}")

    # Crea il deployment
    r = requests.post(f"{BASE_URL}/v2/lm/deployments",
                      headers=headers(token),
                      json={"configurationId": config_id}, timeout=15)
    if not r.ok:
        print(f"  ⚠️  Deployment fallito: {r.text}")
        return ""
    dep_id = r.json()["id"]
    print(f"  Deployment avviato: {dep_id} — attendo che diventi RUNNING...")

    # Aspetta max 5 minuti
    for _ in range(30):
        time.sleep(10)
        token = get_token()
        r = requests.get(f"{BASE_URL}/v2/lm/deployments/{dep_id}",
                         headers=headers(token), timeout=15)
        status = r.json().get("status", "")
        print(f"    Status: {status}")
        if status == "RUNNING":
            return dep_id
        if status in ("DEAD", "STOPPED", "ERROR"):
            print(f"  ❌ Deployment in stato {status}")
            return ""

    print("  ⚠️  Timeout: deployment non ancora RUNNING dopo 5 minuti")
    return dep_id


def main():
    print("=== i-PMP — Setup Deployment SAP AI Core ===\n")
    token = get_token()
    print("✅ Token ottenuto\n")

    print(f"🔍 Chat model: {CHAT_MODEL}")
    chat_id = find_or_create_deployment(token, CHAT_MODEL)

    print(f"\n🔍 Embed model: {EMBED_MODEL}")
    token = get_token()
    embed_id = find_or_create_deployment(token, EMBED_MODEL)

    print("\n=== RISULTATO ===")
    print(f"CHAT_DEPLOYMENT_ID={chat_id}")
    print(f"EMBED_DEPLOYMENT_ID={embed_id}")
    print("\nCopia questi valori nel tuo .env e sei pronto!")


if __name__ == "__main__":
    main()
