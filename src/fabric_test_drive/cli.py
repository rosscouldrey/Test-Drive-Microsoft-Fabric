import argparse
import json
from pathlib import Path
import sys
from uuid import UUID, uuid4

import requests

from .api import DeploymentError, Fabric, login
from .artifacts import ENTITIES, SOURCE, notebook, pipeline

STATE = Path(".fabric-test-drive/state.json")


def save(state):
    STATE.parent.mkdir(parents=True, exist_ok=True)
    temporary = STATE.with_suffix(".tmp")
    temporary.write_text(json.dumps(state, indent=2) + "\n")
    temporary.replace(STATE)


def load():
    if not STATE.exists():
        return None
    state = json.loads(STATE.read_text())
    if state.get("version") != 1:
        raise DeploymentError("Unrecognized deployment state version; do not overwrite it.")
    return state


def guid(value):
    try:
        return str(UUID(value))
    except ValueError as exc:
        raise DeploymentError(f"Expected a tenant/capacity UUID, got: {value!r}") from exc


def choose(capacities):
    active = [c for c in capacities if c["state"] == "Active"]
    if not active:
        raise DeploymentError("No accessible active Fabric capacity. Check capacity access/state.")
    for index, capacity in enumerate(active, 1):
        print(f"  {index}. {capacity['displayName']} ({capacity['sku']}) - {capacity['id']}")
    answer = input("Capacity number: ").strip()
    if not answer.isdecimal() or not 1 <= int(answer) <= len(active):
        raise DeploymentError("Select one of the displayed capacity numbers.")
    return active[int(answer) - 1]["id"]


def connection_body(types, name):
    candidates = [t for t in types if t["type"].casefold() == "odata"]
    for candidate in candidates:
        if "Anonymous" not in candidate["supportedCredentialTypes"]:
            continue
        if "Encrypted" not in candidate["supportedConnectionEncryptionTypes"]:
            continue
        for method in candidate["creationMethods"]:
            parameters = method["parameters"]
            urls = [p for p in parameters if p["name"].casefold() == "url"]
            unsupported = [
                p for p in parameters if p.get("required") and p not in urls
            ]
            if len(urls) != 1 or urls[0]["dataType"] != "Text" or unsupported:
                continue
            return {
                "connectivityType": "ShareableCloud",
                "displayName": name,
                "connectionDetails": {
                    "type": candidate["type"],
                    "creationMethod": method["name"],
                    "parameters": [{
                        "name": urls[0]["name"], "dataType": "Text", "value": SOURCE,
                    }],
                },
                "privacyLevel": "Public",
                "credentialDetails": {
                    "singleSignOnType": "None",
                    "connectionEncryption": "Encrypted",
                    "skipTestConnection": False,
                    "credentials": {"credentialType": "Anonymous"},
                },
            }
    raise DeploymentError(
        "The connection API did not advertise a supported anonymous, encrypted OData "
        "URL creation method. This is a feasibility blocker; no REST/Python ingestion "
        "fallback will be substituted."
    )


def preflight(api):
    capacities = api.list("capacities")
    types = api.list("connections/supportedConnectionTypes?showAllCreationMethods=true")
    connection_body(types, "preflight-only")
    try:
        response = requests.get(
            SOURCE + "Customers",
            params={"$top": "1", "$select": "CustomerID"},
            timeout=30, allow_redirects=False,
        )
        response.raise_for_status()
    except requests.RequestException as exc:
        raise DeploymentError(
            "The public Northwind service is unavailable. No Fabric resources were created."
        ) from exc
    if not 200 <= response.status_code < 300 or not response.json().get("value"):
        raise DeploymentError("Northwind did not return a customer. No resources were created.")
    print("Preflight passed: Fabric API access, connection metadata, Northwind availability.")
    print("Workspace creation permission and connector execution are verified only during deployment.")
    return capacities, types


def owned_workspace(api, state):
    workspace = api.request("GET", f"workspaces/{state['workspace_id']}").json()
    if workspace.get("description") != state["marker"]:
        raise DeploymentError("Workspace ownership marker differs. Refusing to modify or delete it.")
    if workspace.get("capacityId") != state["capacity_id"]:
        raise DeploymentError("Workspace capacity changed. Inspect it before resuming or cleaning up.")
    return workspace


def create_recorded(api, state, key, path, body):
    state["pending_create"] = key
    save(state)
    item = api.create(path, body)
    state[key] = item["id"]
    del state["pending_create"]
    save(state)
    return item["id"]


def ensure_item(api, state, key, kind, name, content=None):
    workspace = state["workspace_id"]
    if key in state:
        item = api.request("GET", f"workspaces/{workspace}/items/{state[key]}").json()
        if item["type"] != kind or item["displayName"] != name:
            raise DeploymentError(f"Recorded {kind} differs from deployment state; refusing to overwrite.")
        return item["id"]
    body = {"displayName": name, "type": kind}
    if content is not None:
        body["definition"] = content
    item_id = create_recorded(
        api, state, key, f"workspaces/{workspace}/items", body
    )
    print(f"Created {kind}: {name}")
    return item_id


def run_stage(api, state, name, item, job_type):
    if name in state["completed"]:
        print(f"Reusing completed stage: {name}")
        return
    location = state["jobs"].get(name)
    if location:
        previous = api.request("GET", location).json()["status"]
        if previous in ("Failed", "Cancelled", "Deduped"):
            print(f"Previous {name} job was {previous}; starting a new attempt.")
            location = None
    if location:
        print(f"Resuming {name}: {location}")
        api.wait_job(location)
    else:
        state["pending_job"] = name
        save(state)

        def remember(url):
            state["jobs"][name] = url
            del state["pending_job"]
            save(state)
            print(f"Running {name}: {url}")

        api.run(state["workspace_id"], item, job_type, remember)
    state["completed"].append(name)
    save(state)


def deploy(api, state, tenant, capacity, name, types):
    if state and state.get("pending_job"):
        raise DeploymentError(
            f"Previous job submission has an unresolved outcome: {state['pending_job']}. "
            "Inspect Fabric jobs and reconcile state before submitting another job."
        )
    if state and state.get("pending_create"):
        raise DeploymentError(
            f"Previous create has an unresolved outcome: {state['pending_create']}. "
            "Inspect Fabric and deployment state before retrying; no write was repeated."
        )
    if state is None:
        if not name or len(name) > 100:
            raise DeploymentError("Workspace name must contain 1-100 characters.")
        existing = api.list("workspaces")
        if any(w["displayName"].casefold() == name.casefold() for w in existing):
            raise DeploymentError("A workspace with that name already exists. Choose a new name.")
        identifier = str(uuid4())
        state = {
            "version": 1, "tenant_id": tenant, "capacity_id": capacity,
            "workspace_name": name, "marker": f"Fabric Test Drive deployment {identifier}",
            "connection_name": f"Northwind-{identifier}",
            "completed": [], "jobs": {}, "verified": False,
        }
        save(state)
    if state.get("verified"):
        state.update(completed=[], jobs={}, verified=False)
        save(state)
    if "workspace_id" not in state:
        create_recorded(api, state, "workspace_id", "workspaces", {
            "displayName": state["workspace_name"],
            "description": state["marker"], "capacityId": state["capacity_id"],
        })
        print(f"Created workspace: {state['workspace_name']}")
    owned_workspace(api, state)
    workspace = state["workspace_id"]
    lakehouse = ensure_item(api, state, "lakehouse_id", "Lakehouse", "Northwind")
    if "connection_id" not in state:
        create_recorded(
            api, state, "connection_id", "connections",
            connection_body(types, state["connection_name"]),
        )
        print("Created anonymous Northwind OData connection.")
    else:
        connection = api.request("GET", f"connections/{state['connection_id']}").json()
        if connection["displayName"] != state["connection_name"]:
            raise DeploymentError("Recorded OData connection was renamed; refusing to reuse it.")
    ingestion = ensure_item(
        api, state, "pipeline_id", "DataPipeline", "Ingest Northwind",
        pipeline(workspace, lakehouse, state["connection_id"]),
    )
    verification = ensure_item(
        api, state, "notebook_id", "Notebook", "Verify Northwind",
        notebook(workspace, lakehouse),
    )
    run_stage(api, state, "ingestion", ingestion, "Pipeline")
    run_stage(api, state, "verification", verification, "RunNotebook")
    state["verified"] = True
    save(state)
    print(f"\nData spike verified: https://app.fabric.microsoft.com/groups/{workspace}/list")
    print("Eight source tables and fact_sales passed notebook assertions.")
    print("Semantic model/report deployment is NOT implemented or verified in this spike.")


def cleanup(api, state):
    if state.get("pending_job"):
        raise DeploymentError(
            "An unresolved job submission may still be running. Reconcile it before cleanup."
        )
    if state.get("pending_create"):
        raise DeploymentError(
            "An unresolved create may have left an unrecorded resource. "
            "Reconcile it in Fabric and deployment state before cleanup."
        )
    if "workspace_id" in state and not state.get("workspace_deleted"):
        owned_workspace(api, state)
    if "connection_id" in state and not state.get("connection_deleted"):
        connection = api.request("GET", f"connections/{state['connection_id']}").json()
        if connection["displayName"] != state["connection_name"]:
            raise DeploymentError("Connection ownership check failed; refusing cleanup.")
    print("This deletes the recorded workspace, ALL its contents, and its OData connection.")
    print("It never deletes or pauses a capacity.")
    if input(f"Type {state['workspace_name']!r} to confirm: ") != state["workspace_name"]:
        raise DeploymentError("Cleanup cancelled; nothing was deleted.")
    if "workspace_id" in state and not state.get("workspace_deleted"):
        for job in state["jobs"].values():
            status = api.request("GET", job).json()["status"]
            if status not in ("Completed", "Failed", "Cancelled", "Deduped"):
                raise DeploymentError("A recorded job is still running. Cancel it in Fabric before cleanup.")
        api.delete(f"workspaces/{state['workspace_id']}")
        state["workspace_deleted"] = True
        save(state)
    if "connection_id" in state and not state.get("connection_deleted"):
        api.delete(f"connections/{state['connection_id']}")
        state["connection_deleted"] = True
        save(state)
    STATE.unlink()
    print("Recorded resources deleted. Capacity left unchanged.")


def main(argv=None):
    parser = argparse.ArgumentParser(description="Experimental Northwind Fabric deployment")
    parser.add_argument("command", choices=("plan", "preflight", "deploy", "cleanup"))
    parser.add_argument("--tenant", help="Microsoft Entra tenant UUID")
    parser.add_argument("--capacity", help="Existing active Fabric capacity UUID")
    parser.add_argument("--name", help="New workspace display name")
    parser.add_argument("--skip-login", action="store_true", help="Use an existing Azure CLI session")
    args = parser.parse_args(argv)
    if args.command == "plan":
        print("EXPERIMENTAL: Northwind deployment feasibility spike")
        print("Create workspace -> lakehouse -> anonymous OData connection -> ingestion pipeline")
        print(f"Copy {len(ENTITIES)} entities -> run verification/transformation notebook -> fact_sales")
        print("No model/report yet. No capacity provisioning. No writes in plan/preflight.")
        return 0
    try:
        state = load()
        if args.command == "cleanup" and state is None:
            raise DeploymentError("No local deployment state. Nothing will be deleted.")
        if state and (state.get("workspace_deleted") or state.get("connection_deleted")) and args.command != "cleanup":
            raise DeploymentError("Cleanup is incomplete. Finish cleanup before deploying again.")
        tenant = guid(args.tenant or (state or {}).get("tenant_id") or input("Tenant UUID: ").strip())
        if state and tenant != state["tenant_id"]:
            raise DeploymentError("Tenant differs from saved state. Use the original tenant.")
        if state and args.capacity and guid(args.capacity) != state["capacity_id"]:
            raise DeploymentError("Capacity differs from saved state; refusing to switch it.")
        if state and args.name and args.name != state["workspace_name"]:
            raise DeploymentError("Workspace name differs from saved state; refusing to switch it.")
        if not args.skip_login:
            login(tenant)
        api = Fabric(tenant)
        if args.command == "cleanup":
            cleanup(api, state)
            return 0
        capacities, types = preflight(api)
        capacity = guid(args.capacity or (state or {}).get("capacity_id") or choose(capacities))
        if not any(c["id"] == capacity and c["state"] == "Active" for c in capacities):
            raise DeploymentError("Selected capacity is not accessible and active.")
        if args.command == "preflight":
            print(f"Selected capacity: {capacity}. No resources created.")
            return 0
        name = args.name or (state or {}).get("workspace_name") or input("New workspace name: ").strip()
        if state is None:
            print(f"Will create a workspace named {name!r} on capacity {capacity}.")
            print("Fabric consumption can incur charges. Source tables are overwritten on reruns.")
            if input("Type deploy to continue: ").strip() != "deploy":
                raise DeploymentError("Deployment cancelled; no resources were created.")
        deploy(api, state, tenant, capacity, name, types)
        return 0
    except (DeploymentError, OSError, ValueError, KeyError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        print("Deployment did not complete. Preserve .fabric-test-drive/state.json if it exists.", file=sys.stderr)
        return 1
    except (KeyboardInterrupt, EOFError):
        print("\nStopped. Preserve deployment state before closing the Codespace.", file=sys.stderr)
        return 130
