import base64
import json
from pathlib import Path
import re
from uuid import UUID

import requests

from .api import DeploymentError, azure


MODEL_TEMPLATE = Path(__file__).resolve().parents[2] / "templates/Northwind.SemanticModel"
ONELAKE_SOURCE = re.compile(
    r'https://onelake\.dfs\.fabric\.microsoft\.com/'
    r'[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}/'
    r'[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}(?=")'
)


def ensure_large_storage(tenant, workspace, model):
    tenant, workspace, model = (str(UUID(value)) for value in (tenant, workspace, model))
    url = f"https://api.powerbi.com/v1.0/myorg/groups/{workspace}/datasets/{model}"
    token = json.loads(azure(
        "account", "get-access-token", "--tenant", tenant,
        "--resource", "https://analysis.windows.net/powerbi/api", "--output", "json",
    ))["accessToken"]

    def request(method, body=None):
        try:
            response = requests.request(
                method, url, json=body,
                headers={"Authorization": f"Bearer {token}"},
                timeout=60, allow_redirects=False,
            )
        except requests.RequestException as exc:
            raise DeploymentError(
                "Power BI storage configuration request failed; inspect model settings "
                "and rerun deployment."
            ) from exc
        if not 200 <= response.status_code < 300:
            raise DeploymentError(
                f"Power BI storage configuration: HTTP {response.status_code}. "
                f"{response.text[:1000]}"
            )
        return response

    if request("GET").json().get("targetStorageMode") == "PremiumFiles":
        print("Semantic model already targets large storage (PremiumFiles).")
        return
    request("PATCH", {"targetStorageMode": "PremiumFiles"})
    if request("GET").json().get("targetStorageMode") != "PremiumFiles":
        raise DeploymentError(
            "Semantic model large-storage setting is not yet confirmed. "
            "Inspect its settings and rerun deployment."
        )
    print("Semantic model large storage requested (PremiumFiles); conversion may still be running.")


def model_definition(workspace, lakehouse):
    workspace, lakehouse = str(UUID(workspace)), str(UUID(lakehouse))
    if not (MODEL_TEMPLATE / "definition.pbism").is_file() or not (
        MODEL_TEMPLATE / "definition/expressions.tmdl"
    ).is_file():
        raise DeploymentError(f"Semantic model template is incomplete: {MODEL_TEMPLATE}")
    parts = []
    for path in sorted(MODEL_TEMPLATE.rglob("*")):
        if path.is_symlink():
            raise DeploymentError(f"Semantic model template contains a symlink: {path}")
        if not path.is_file() or path.name == ".platform":
            continue
        relative = path.relative_to(MODEL_TEMPLATE).as_posix()
        content = path.read_bytes()
        if relative == "definition/expressions.tmdl":
            text = content.decode("utf-8")
            text, count = ONELAKE_SOURCE.subn(
                f"https://onelake.dfs.fabric.microsoft.com/{workspace}/{lakehouse}",
                text,
            )
            if count != 1 or text.lower().count("onelake.dfs.fabric.microsoft.com") != 1:
                raise DeploymentError(
                    "Expected exactly one OneLake source in the semantic model template; "
                    "inspect its data sources before deployment."
                )
            content = text.encode("utf-8")
        elif b"onelake.dfs.fabric.microsoft.com" in content.lower():
            raise DeploymentError(
                f"Unexpected OneLake source in {relative}; inspect it before deployment."
            )
        parts.append({
            "path": relative, "payload": base64.b64encode(content).decode("ascii"),
            "payloadType": "InlineBase64",
        })
    return {"format": "TMDL", "parts": parts}
