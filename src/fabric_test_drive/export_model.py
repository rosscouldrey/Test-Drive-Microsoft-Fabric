import argparse
import base64
from pathlib import Path, PurePosixPath
import sys

from .api import DeploymentError, Fabric
from .cli import guid, load


def export_model(api, workspace, model, destination):
    if destination.exists():
        raise DeploymentError(
            f"Export destination already exists: {destination}. "
            "Use a new --output directory, then review and replace the previous export."
        )
    response = api.request(
        "POST", f"workspaces/{workspace}/semanticModels/{model}/getDefinition?format=TMDL"
    )
    if response.status_code == 202:
        data = api.wait_operation(response, "Semantic model export", result=True)
    else:
        data = response.json()
    definition = data.get("definition") if isinstance(data, dict) else None
    parts = definition.get("parts") if isinstance(definition, dict) else None
    if not isinstance(parts, list) or not parts:
        raise DeploymentError("Fabric returned no semantic model definition parts.")
    files = {}
    for part in parts:
        if not isinstance(part, dict):
            raise DeploymentError("Fabric returned an invalid definition part.")
        name = part.get("path")
        if not isinstance(name, str) or not name:
            raise DeploymentError("Fabric returned an invalid definition part path.")
        path = PurePosixPath(name)
        if (
            path.is_absolute()
            or "\\" in name
            or any(segment in ("", ".", "..") for segment in name.split("/"))
            or path in files
        ):
            raise DeploymentError(f"Unsafe or duplicate definition part path: {name!r}")
        if part.get("payloadType") != "InlineBase64":
            raise DeploymentError(f"Unsupported payload type for definition part: {name}")
        try:
            files[path] = base64.b64decode(part["payload"], validate=True)
        except (KeyError, ValueError, TypeError) as exc:
            raise DeploymentError(f"Invalid Base64 definition part: {name}") from exc
    for path in files:
        if any(parent in files for parent in path.parents):
            raise DeploymentError(f"Conflicting definition part path: {path}")
    destination.mkdir(parents=True, exist_ok=False)
    for path, content in files.items():
        target = destination.joinpath(*path.parts)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
    print(f"Exported {len(files)} semantic model definition files to {destination}.")
    print("Review data-source references and sensitive values before committing.")
    print("This command exports definitions only; deploy uses the template and rebinds its lakehouse.")


def main(argv=None):
    parser = argparse.ArgumentParser(description="Export a Fabric semantic model as TMDL files")
    parser.add_argument("--model", required=True, help="Semantic model UUID from its Fabric URL")
    parser.add_argument("--tenant", help="Tenant UUID (defaults to local deployment state)")
    parser.add_argument("--workspace", help="Workspace UUID (defaults to local deployment state)")
    parser.add_argument(
        "--output", type=Path, default=Path("templates/Northwind.SemanticModel"),
        help="New export directory (default: templates/Northwind.SemanticModel)",
    )
    args = parser.parse_args(argv)
    try:
        state = (load() or {}) if not args.tenant or not args.workspace else {}
        tenant = args.tenant or state.get("tenant_id")
        workspace = args.workspace or state.get("workspace_id")
        if not tenant or not workspace:
            raise DeploymentError(
                "Provide --tenant and --workspace, or run from a directory with deployment state."
            )
        export_model(Fabric(guid(tenant)), guid(workspace), guid(args.model), args.output)
        return 0
    except (DeploymentError, OSError, ValueError, KeyError) as exc:
        print(f"Export failed: {exc}", file=sys.stderr)
        return 1
    except (KeyboardInterrupt, EOFError):
        print("\nExport stopped.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())
