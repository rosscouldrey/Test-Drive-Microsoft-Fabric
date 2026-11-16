import base64
import json
from pathlib import Path
from uuid import UUID

from .api import DeploymentError, PowerBI


REPORT_TEMPLATE = Path(__file__).resolve().parents[2] / "templates/Northwind.Report"


def report_definition(model):
    model = str(UUID(model))
    if not (REPORT_TEMPLATE / "definition.pbir").is_file() or not (
        (REPORT_TEMPLATE / "definition/report.json").is_file()
        or (REPORT_TEMPLATE / "report.json").is_file()
    ):
        raise DeploymentError(f"Report template is incomplete: {REPORT_TEMPLATE}")
    parts = []
    for path in sorted(REPORT_TEMPLATE.rglob("*")):
        if path.is_symlink():
            raise DeploymentError(f"Report template contains a symlink: {path}")
        if not path.is_file() or path.name == ".platform":
            continue
        relative = path.relative_to(REPORT_TEMPLATE).as_posix()
        content = path.read_bytes()
        if relative == "definition.pbir":
            definition = json.loads(content)
            if not isinstance(definition, dict):
                raise DeploymentError("Report definition.pbir must contain a JSON object.")
            definition["datasetReference"] = {
                "byConnection": {"connectionString": f"semanticmodelid={model}"},
            }
            content = (json.dumps(definition, indent=2) + "\n").encode("utf-8")
        parts.append({
            "path": relative, "payload": base64.b64encode(content).decode("ascii"),
            "payloadType": "InlineBase64",
        })
    return {"parts": parts}


def verify_report(tenant, state):
    workspace = str(UUID(state["workspace_id"]))
    report = str(UUID(state["report_id"]))
    model = str(UUID(state["semantic_model_id"]))
    data = PowerBI(tenant).request("GET", f"groups/{workspace}/reports/{report}").json()
    if data.get("datasetId") != model:
        raise DeploymentError(
            "Report is not bound to the recorded semantic model; inspect it before proceeding."
        )
    print("Report binding verified; visual rendering still requires a manual check.")
