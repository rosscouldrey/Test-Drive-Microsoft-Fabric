import argparse
import json
from pathlib import Path
import sys
from urllib.parse import urlsplit
from uuid import UUID, uuid4

import requests

from .api import DeploymentError, Fabric, FabricApiError, PowerBI, login
from .app import check_prerequisites, deploy_app
from .artifacts import ENTITIES, SOURCE, notebook, pipeline, schema_notebook
from .model import model_definition, refresh_status, verify_model
from .report import report_definition, verify_report

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
    if urlsplit(SOURCE).scheme != "https":
        raise DeploymentError("The Northwind OData source must use HTTPS.")
    candidates = [t for t in types if t["type"].casefold() == "odata"]
    for candidate in candidates:
        if "Anonymous" not in candidate["supportedCredentialTypes"]:
            continue
        encryption = next(
            (value for value in ("Encrypted", "NotEncrypted")
             if value in candidate["supportedConnectionEncryptionTypes"]),
            None,
        )
        if encryption is None:
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
                    "connectionEncryption": encryption,
                    "skipTestConnection": False,
                    "credentials": {"credentialType": "Anonymous"},
                },
            }
    raise DeploymentError(
        "The connection API did not advertise a supported anonymous OData "
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


def ensure_item(api, state, key, kind, name, content=None, creation_payload=None):
    workspace = state["workspace_id"]
    if key in state:
        item = api.request("GET", f"workspaces/{workspace}/items/{state[key]}").json()
        if item["type"] != kind or item["displayName"] != name:
            raise DeploymentError(f"Recorded {kind} differs from deployment state; refusing to overwrite.")
        return item["id"]
    body = {"displayName": name, "type": kind}
    if content is not None:
        body["definition"] = content
    if creation_payload is not None:
        body["creationPayload"] = creation_payload
    item_id = create_recorded(
        api, state, key, f"workspaces/{workspace}/items", body
    )
    print(f"Created {kind}: {name}")
    return item_id


def run_stage(api, state, name, item, job_type, definition=None):
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
        if definition is not None:
            api.update_definition(state["workspace_id"], item, definition)
            print(f"Updated {name} definition before starting a new run.")
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


def deploy(api, state, tenant, capacity, name, types, with_app=False):
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
    if state and state.get("lakehouse_layout") != "bronze-gold-schemas":
        raise DeploymentError(
            "This deployment uses a legacy non-schema lakehouse and cannot be upgraded in place. "
            "Keep its state for cleanup, or deploy from a separate directory with a new workspace name."
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
            "lakehouse_layout": "bronze-gold-schemas",
            "app_requested": with_app,
        }
        save(state)
    elif state.get("app_requested", False) != with_app:
        state["app_requested"] = with_app
        save(state)
    if state.get("verified"):
        state.update(completed=[], jobs={}, verified=False)
        state.pop("model_refresh_id", None)
        save(state)
    if "workspace_id" not in state:
        create_recorded(api, state, "workspace_id", "workspaces", {
            "displayName": state["workspace_name"],
            "description": state["marker"], "capacityId": state["capacity_id"],
        })
        print(f"Created workspace: {state['workspace_name']}")
    owned_workspace(api, state)
    workspace = state["workspace_id"]
    lakehouse = ensure_item(
        api, state, "lakehouse_id", "Lakehouse", "Northwind",
        creation_payload={"enableSchemas": True},
    )
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
    existing_pipeline = "pipeline_id" in state
    ingestion_definition = pipeline(workspace, lakehouse, state["connection_id"])
    ingestion = ensure_item(
        api, state, "pipeline_id", "DataPipeline", "Ingest Northwind",
        ingestion_definition,
    )
    existing_notebook = "notebook_id" in state
    notebook_definition = notebook(workspace, lakehouse)
    verification = ensure_item(
        api, state, "notebook_id", "Notebook", "Verify Northwind",
        notebook_definition,
    )
    setup = ensure_item(
        api, state, "schema_notebook_id", "Notebook", "Initialize Schemas",
        schema_notebook(workspace, lakehouse),
    )
    run_stage(api, state, "schema_setup", setup, "RunNotebook")
    run_stage(
        api, state, "ingestion", ingestion, "Pipeline",
        definition=ingestion_definition if existing_pipeline else None,
    )
    run_stage(
        api, state, "verification", verification, "RunNotebook",
        definition=notebook_definition if existing_notebook else None,
    )
    if "semantic_model_id" in state:
        ensure_item(api, state, "semantic_model_id", "SemanticModel", "Northwind Model")
    else:
        definition = model_definition(workspace, lakehouse)
        ensure_item(api, state, "semantic_model_id", "SemanticModel", "Northwind Model", definition)
    verify_model(tenant, state, save)
    if "report_id" in state:
        ensure_item(api, state, "report_id", "Report", "Northwind Report")
    else:
        ensure_item(
            api, state, "report_id", "Report", "Northwind Report",
            report_definition(state["semantic_model_id"]),
        )
    verify_report(tenant, state)
    state["verified"] = True
    save(state)
    print("\nDeployment complete.")
    print(f"Workspace: https://app.fabric.microsoft.com/groups/{workspace}/list")
    print("Eight bronze source tables and five gold tables passed notebook assertions.")
    print("Semantic model refresh completed; Total Sales and Order Count returned positive values.")
    print(f"Report: https://app.powerbi.com/groups/{workspace}/reports/{state['report_id']}")
    print("Report binding verified. Open it to check visual rendering and interactions.")


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
        try:
            owned_workspace(api, state)
        except FabricApiError as exc:
            if exc.status_code != 404 or exc.error_code != "WorkspaceNotFound":
                raise
            state["workspace_deleted"] = True
            save(state)
            print("Recorded workspace was not found; treating it as already deleted.")
    if "connection_id" in state and not state.get("connection_deleted"):
        connection = api.request("GET", f"connections/{state['connection_id']}").json()
        if connection["displayName"] != state["connection_name"]:
            raise DeploymentError("Connection ownership check failed; refusing cleanup.")
    print("This deletes the recorded workspace, ALL its contents, and its OData connection.")
    print("It never deletes or pauses a capacity.")
    if input(f"Type {state['workspace_name']!r} to confirm: ") != state["workspace_name"]:
        raise DeploymentError("Cleanup cancelled; nothing was deleted.")
    if "workspace_id" in state and not state.get("workspace_deleted"):
        if state.get("model_refresh_id"):
            status = refresh_status(PowerBI(state["tenant_id"]), state).json()["status"]
            if status not in ("Completed", "Failed", "Cancelled", "Disabled", "TimedOut"):
                raise DeploymentError(
                    "A model refresh is still running. Wait for it or cancel it in Fabric before cleanup."
                )
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
    parser = argparse.ArgumentParser(description="Deploy the Northwind Microsoft Fabric demo")
    parser.add_argument("command", choices=("plan", "preflight", "deploy", "cleanup"))
    parser.add_argument("--tenant", help="Microsoft Entra tenant UUID")
    parser.add_argument("--capacity", help="Existing active Fabric capacity UUID")
    parser.add_argument("--name", help="New workspace display name")
    parser.add_argument("--skip-login", action="store_true", help="Use an existing Azure CLI session")
    app_options = parser.add_mutually_exclusive_group()
    app_options.add_argument(
        "--with-app", action="store_true", default=None, help="Also deploy the optional Fabric App",
    )
    app_options.add_argument(
        "--without-app", dest="with_app", action="store_false",
        help="Skip the optional Fabric App and its selection prompt",
    )
    args = parser.parse_args(argv)
    if args.with_app is not None and args.command != "deploy":
        parser.error("--with-app and --without-app are only supported with deploy")
    if args.command == "plan":
        print("Northwind Microsoft Fabric Test Drive deployment plan")
        print("Create workspace -> schema-enabled lakehouse -> bronze and gold schemas")
        print(f"Copy {len(ENTITIES)} entities to bronze -> notebook builds five gold tables")
        print("Create semantic model -> large storage -> refresh with retries -> verify Sales and Orders")
        print("Create report bound to semantic model -> verify report binding")
        print("Optional Fabric App: choose at first interactive deploy or pass deploy --with-app")
        print("No capacity provisioning. No writes in plan/preflight.")
        return 0
    try:
        state = load()
        if args.command == "cleanup" and state is None:
            raise DeploymentError("No local deployment state. Nothing will be deleted.")
        if state and (state.get("workspace_deleted") or state.get("connection_deleted")) and args.command != "cleanup":
            raise DeploymentError("Cleanup is incomplete. Finish cleanup before deploying again.")
        if args.command == "deploy" and state and state.get("pending_app"):
            raise DeploymentError("The previous app deployment has an unresolved outcome; inspect it before retrying.")
        tenant = guid(args.tenant or (state or {}).get("tenant_id") or input("Tenant UUID: ").strip())
        if state and tenant != state["tenant_id"]:
            raise DeploymentError("Tenant differs from saved state. Use the original tenant.")
        if state and args.capacity and guid(args.capacity) != state["capacity_id"]:
            raise DeploymentError("Capacity differs from saved state; refusing to switch it.")
        if state and args.name and args.name != state["workspace_name"]:
            raise DeploymentError("Workspace name differs from saved state; refusing to switch it.")
        with_app = (state or {}).get("app_requested", False) if args.with_app is None else args.with_app
        if args.command == "deploy":
            if state is None and args.with_app is None and sys.stdin.isatty():
                answer = input("Include the optional Northwind Fabric App? [y/N]: ").strip().casefold()
                if answer not in ("", "n", "no", "y", "yes"):
                    raise DeploymentError("Enter yes or no for the optional app.")
                with_app = answer in ("y", "yes")
            if with_app and not (state or {}).get("app_id"):
                check_prerequisites()
        if not args.skip_login:
            login(tenant)
        api = Fabric(tenant)
        if args.command == "cleanup":
            cleanup(api, state)
            return 0
        if args.command == "deploy" and with_app and state and state.get("verified"):
            if state.get("pending_job") or state.get("pending_create"):
                raise DeploymentError("Unresolved deployment operation; reconcile it before deploying the app.")
            if not state.get("semantic_model_id") or not state.get("report_id"):
                raise DeploymentError("Completed deployment state is missing the model or report ID.")
            owned_workspace(api, state)
            ensure_item(api, state, "semantic_model_id", "SemanticModel", "Northwind Model")
            ensure_item(api, state, "report_id", "Report", "Northwind Report")
            state["app_requested"] = True
            save(state)
            deploy_app(api, state, save)
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
        deploy(api, state, tenant, capacity, name, types, with_app=with_app)
        if with_app:
            deploy_app(api, load(), save)
        return 0
    except (DeploymentError, OSError, ValueError, KeyError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        print("Deployment did not complete. Preserve .fabric-test-drive/state.json if it exists.", file=sys.stderr)
        return 1
    except (KeyboardInterrupt, EOFError):
        print("\nStopped. Preserve deployment state before closing the Codespace.", file=sys.stderr)
        return 130
