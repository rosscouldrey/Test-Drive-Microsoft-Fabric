import argparse
import base64
from pathlib import Path, PurePosixPath
import sys

from .api import DeploymentError, Fabric
from .cli import guid, load


def export_report(api, workspace, report, destination):
    if destination.exists():
        raise DeploymentError(
            f"Export destination already exists: {destination}. "
            "Use a new --output directory, then review and replace the previous export."
        )
    response = api.request(
        "POST", f"workspaces/{workspace}/reports/{report}/getDefinition"
    )
    data = (
        api.wait_operation(response, "Report export", result=True)
        if response.status_code == 202 else response.json()
    )
    definition = data.get("definition") if isinstance(data, dict) else None
    parts = definition.get("parts") if isinstance(definition, dict) else None
    if not isinstance(parts, list) or not parts:
        raise DeploymentError("Fabric returned no report definition parts.")
    files = {}
    for part in parts:
        if not isinstance(part, dict):
            raise DeploymentError("Fabric returned an invalid report definition part.")
        name = part.get("path")
        if not isinstance(name, str) or not name:
            raise DeploymentError("Fabric returned an invalid report definition part path.")
        path = PurePosixPath(name)
        if (
            path.is_absolute()
            or "\\" in name
            or any(segment in ("", ".", "..") for segment in name.split("/"))
            or path in files
        ):
            raise DeploymentError(f"Unsafe or duplicate report definition path: {name!r}")
        if part.get("payloadType") != "InlineBase64":
            raise DeploymentError(f"Unsupported payload type for report definition part: {name}")
        try:
            files[path] = base64.b64decode(part["payload"], validate=True)
        except (KeyError, ValueError, TypeError) as exc:
            raise DeploymentError(f"Invalid Base64 report definition part: {name}") from exc
    for path in files:
        if any(parent in files for parent in path.parents):
            raise DeploymentError(f"Conflicting report definition path: {path}")
    if not any(path.name == "definition.pbir" for path in files) and not any(
        path.name == "report.json" for path in files
    ):
        raise DeploymentError("Report definition has neither PBIR nor PBIR-Legacy entry point.")
    destination.mkdir(parents=True, exist_ok=False)
    for path, content in files.items():
        target = destination.joinpath(*path.parts)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
    print(f"Exported {len(files)} report definition files to {destination}.")
    print("Inspect definition.pbir for the semantic-model reference before committing.")
    print("This exports definitions only; report deployment and model rebinding are not implemented.")


def main(argv=None):
    parser = argparse.ArgumentParser(description="Export a Fabric report as PBIR files")
    parser.add_argument("--report", required=True, help="Report UUID from its Fabric URL")
    parser.add_argument("--tenant", help="Tenant UUID (defaults to local deployment state)")
    parser.add_argument("--workspace", help="Workspace UUID (defaults to local deployment state)")
    parser.add_argument(
        "--output", type=Path, default=Path("templates/Northwind.Report"),
        help="New export directory (default: templates/Northwind.Report)",
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
        export_report(Fabric(guid(tenant)), guid(workspace), guid(args.report), args.output)
        return 0
    except (DeploymentError, OSError, ValueError, KeyError) as exc:
        print(f"Report export failed: {exc}", file=sys.stderr)
        return 1
    except (KeyboardInterrupt, EOFError):
        print("\nReport export stopped.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())
