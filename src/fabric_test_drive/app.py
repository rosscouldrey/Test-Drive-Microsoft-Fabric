import json
from pathlib import Path
import shutil
import subprocess
from uuid import UUID

from .api import DeploymentError

APP_NAME = "Northwind Fabric App"
SOURCE = Path("apps/northwind-fabric-app")
STAGING = Path(".fabric-test-drive/app")


def command(args, *, capture=False):
    try:
        result = subprocess.run(
            args, cwd=STAGING, text=True, check=False,
            capture_output=capture,
        )
    except FileNotFoundError as exc:
        raise DeploymentError(f"Required command not found: {args[0]}") from exc
    if result.returncode:
        if capture:
            print(result.stderr or result.stdout)
        raise DeploymentError(f"Command failed: {args[0]} {' '.join(args[1:3])}")
    return result.stdout if capture else None


def json_command(args):
    output = command(args, capture=True).lstrip()
    # Rayfin 1.36.2 writes this authentication notice to stdout even in JSON mode.
    notice = "[rayfin] using ambient token from RAYFIN_TOKEN"
    while output.splitlines() and output.splitlines()[0] == notice:
        output = output.partition("\n")[2].lstrip()
    try:
        result = json.loads(output)
    except json.JSONDecodeError as exc:
        raise DeploymentError("Rayfin returned invalid JSON; inspect the deployment before retrying.") from exc
    if not isinstance(result, dict):
        raise DeploymentError("Rayfin returned an unexpected JSON shape; inspect the deployment before retrying.")
    return result


def prepare():
    if not SOURCE.joinpath("rayfin/rayfin.yml").is_file():
        raise DeploymentError("App source not found. Run from the repository root.")
    if not STAGING.exists():
        def ignored(_directory, names):
            return {
                name for name in names
                if name in {"node_modules", "dist", ".git", ".deployments.json", ".temp", ".vite"}
                or name.startswith(".env") or name.endswith(".tsbuildinfo")
            }
        shutil.copytree(SOURCE, STAGING, ignore=ignored)
    if not STAGING.joinpath("package-lock.json").is_file() or not STAGING.joinpath("rayfin/rayfin.yml").is_file():
        raise DeploymentError("The staged app is incomplete; inspect .fabric-test-drive/app before retrying.")
    version = command(["node", "--version"], capture=True).strip().lstrip("v").split(".")[0]
    if version not in {"20", "22", "24"}:
        raise DeploymentError("The Fabric App requires Node.js 20, 22, or 24.")
    installed = STAGING / ".test-drive-npm-installed"
    if not installed.is_file() or not STAGING.joinpath("node_modules/.bin/rayfin").is_file():
        print("Installing the app's locked npm dependencies.")
        command(["npm", "ci"])
        installed.touch()


def deploy_app(api, state, save):
    if state.get("pending_app"):
        raise DeploymentError(
            "The previous app deployment has an unresolved outcome. Inspect the Fabric workspace "
            "and Rayfin deployment registry before retrying; do not blindly repeat it."
        )
    if not state.get("verified") or not state.get("workspace_id") or not state.get("semantic_model_id"):
        raise DeploymentError("Complete the Test Drive deployment before deploying the app.")
    workspace = str(UUID(state["workspace_id"]))
    model = str(UUID(state["semantic_model_id"]))
    tenant = str(UUID(state["tenant_id"]))
    items = api.list(f"workspaces/{workspace}/items")
    matching = [item for item in items if item.get("displayName", "").casefold() == APP_NAME.casefold()]
    recorded = state.get("app_id")
    if matching and (not recorded or len(matching) != 1 or matching[0].get("id") != recorded):
        raise DeploymentError("An unrecorded item with the app name exists; refusing to reuse it.")
    if recorded and (not matching or matching[0].get("type") != "AppBackend"):
        raise DeploymentError("The recorded app was not found or has the wrong type.")
    if recorded:
        raise DeploymentError("The app is already recorded in this deployment; automatic redeployment is not supported.")

    prepare()
    if STAGING.joinpath("rayfin/.deployments.json").exists():
        raise DeploymentError("An existing Rayfin deployment registry needs inspection before creating an app.")
    rayfin = str(Path("node_modules/.bin/rayfin"))
    print("Sign in to Rayfin using the same tenant and account as the Test Drive deployment.")
    command([rayfin, "login", "--tenant", tenant])
    command([
        rayfin, "connector", "add", "--type", "fabric-semanticmodel", "--name", "northwind-model",
        "--workspace-id", workspace, "--item-id", model, "--operations", "executeQuery", "--yes",
    ])
    for script in ("typecheck", "build", "lint", "test", "validate:visual"):
        command(["npm", "run", script])
    target = [rayfin, "up", "--tenant", tenant, "--workspace-id", workspace, "--item-name", APP_NAME]
    preview = json_command(target + ["--dry-run", "--json"])
    plan = preview.get("plan", {})
    if (preview.get("status") != "dry-run" or preview.get("blocked") is not False
            or plan.get("workspaceId") != workspace or plan.get("workspaceName") != state["workspace_name"]
            or plan.get("itemName") != APP_NAME):
        raise DeploymentError("Rayfin preview did not confirm the expected workspace and app.")
    print(f"Rayfin preview: workspace {plan['workspaceName']} ({workspace}); app {APP_NAME}.")
    if input("Type deploy app to deploy the app: ").strip() != "deploy app":
        raise DeploymentError("App deployment cancelled; no app was deployed.")
    state["pending_app"] = True
    save(state)
    result = json_command(target + ["--json"])
    deployment = result.get("deployment", {})
    generated = result.get("generate")
    for warning in result.get("warnings", []):
        print(f"Rayfin warning: {warning}")
    if (result.get("status") != "success" or deployment.get("fabricWorkspaceId") != workspace
            or deployment.get("itemName") != APP_NAME or not deployment.get("rayfinItemId")
            or not deployment.get("hostingUrl")
            or not isinstance(generated, list) or len(generated) != 1
            or generated[0].get("name") != "northwind-model"
            or not (generated[0].get("status") == "success"
                    or (generated[0].get("status") == "skipped"
                        and generated[0].get("skipReason") == "non-graphql-connector"))):
        detail = result.get("error")
        raise DeploymentError(
            f"Rayfin deployment or connector result could not be verified: {detail or 'inspect before retrying'}."
        )
    item_id = str(UUID(deployment["rayfinItemId"]))
    item = api.request("GET", f"workspaces/{workspace}/items/{item_id}").json()
    if item.get("id") != item_id or item.get("type") != "AppBackend" or item.get("displayName") != APP_NAME:
        raise DeploymentError("The deployed app could not be verified in Fabric; inspect it before retrying.")
    state["app_id"] = item_id
    del state["pending_app"]
    save(state)
    print("App deployed; browser sign-in, data, and visual checks are still required.")
    print(f"App hosting URL: {deployment['hostingUrl']}")
