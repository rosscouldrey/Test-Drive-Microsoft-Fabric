import base64
import math
from pathlib import Path
import re
import time
from uuid import UUID

from .api import DeploymentError, PowerBI


MODEL_TEMPLATE = Path(__file__).resolve().parents[2] / "templates/Northwind.SemanticModel"
ONELAKE_SOURCE = re.compile(
    r'https://onelake\.dfs\.fabric\.microsoft\.com/'
    r'[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}/'
    r'[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}(?=")'
)


def dataset_path(state):
    workspace = str(UUID(state["workspace_id"]))
    model = str(UUID(state["semantic_model_id"]))
    return f"groups/{workspace}/datasets/{model}"


def refresh_status(api, state):
    refresh = str(UUID(state["model_refresh_id"]))
    return api.request("GET", f"{dataset_path(state)}/refreshes/{refresh}")


def verify_model(tenant, state, save):
    api = PowerBI(tenant)
    path = dataset_path(state)
    deadline = time.monotonic() + api.timeout
    response = api.request("GET", path)
    if response.json().get("targetStorageMode") != "PremiumFiles":
        api.request("PATCH", path, {"targetStorageMode": "PremiumFiles"})
        while True:
            response = api.request("GET", path)
            if response.json().get("targetStorageMode") == "PremiumFiles":
                break
            api.pause(response, delay=15, deadline=deadline)
    print("Semantic model targets PremiumFiles; waiting for refresh and query readiness.")
    if "model_refresh" not in state["completed"]:
        response = refresh_status(api, state) if state.get("model_refresh_id") else None
        if response is not None and response.json().get("status") in (
            "Failed", "Cancelled", "Disabled", "TimedOut",
        ):
            print("Previous model refresh ended unsuccessfully; starting a new attempt.")
            response = None
        if response is None:
            state["pending_job"] = "model_refresh"
            save(state)
            response = api.request("POST", path + "/refreshes", {
                "type": "full", "commitMode": "transactional",
                "retryCount": 2, "timeout": "00:05:00",
            })
            if response.status_code != 202:
                raise DeploymentError("Model refresh was not accepted; inspect refresh history.")
            location = response.headers.get("Location")
            if not location:
                raise DeploymentError("Model refresh returned no polling URL; inspect refresh history.")
            location = api.url(location)
            parent, refresh = location.rsplit("/", 1)
            if parent != api.url(path + "/refreshes"):
                raise DeploymentError("Unexpected model refresh URL; inspect refresh history.")
            state["model_refresh_id"] = str(UUID(refresh))
            del state["pending_job"]
            save(state)
            print(f"Running model refresh: {state['model_refresh_id']} (up to two service retries).")
            api.pause(response, delay=15, deadline=deadline)
            response = refresh_status(api, state)
        while True:
            data = response.json()
            status = data.get("status")
            if status == "Completed":
                break
            if status not in ("Unknown", "NotStarted", "InProgress"):
                raise DeploymentError(
                    f"Model refresh {status}: {response.text[:1000]}. "
                    "Inspect refresh history; rerun deploy to retry after resolving errors."
                )
            api.pause(response, delay=15, deadline=deadline)
            response = refresh_status(api, state)
        state["completed"].append("model_refresh")
        save(state)
    if "model_query" not in state["completed"]:
        response = api.request("POST", path + "/executeQueries", {
            "queries": [{"query": 'EVALUATE ROW("Sales", [Total Sales], "Orders", [Order Count])'}],
        })
        data = response.json()
        try:
            results = data["results"]
            tables = results[0]["tables"]
            rows = tables[0]["rows"]
            valid = (
                not data.get("error") and not results[0].get("error") and not tables[0].get("error")
                and len(results) == len(tables) == len(rows) == 1
                and all(
                    isinstance(rows[0][key], (int, float))
                    and not isinstance(rows[0][key], bool)
                    and math.isfinite(rows[0][key]) and rows[0][key] > 0
                    for key in ("[Sales]", "[Orders]")
                )
            )
        except (KeyError, IndexError, TypeError):
            valid = False
        if not valid:
            raise DeploymentError(f"Model query did not return positive Sales and Orders: {response.text[:1000]}")
        state["completed"].append("model_query")
        save(state)
        print(f"Verified semantic model: Sales={rows[0]['[Sales]']}, Orders={rows[0]['[Orders]']}.")


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
