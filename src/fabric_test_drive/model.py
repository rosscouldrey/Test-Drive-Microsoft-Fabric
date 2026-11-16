import base64
from pathlib import Path
import re
from uuid import UUID

from .api import DeploymentError


MODEL_TEMPLATE = Path(__file__).resolve().parents[2] / "templates/Northwind.SemanticModel"
ONELAKE_SOURCE = re.compile(
    r'https://onelake\.dfs\.fabric\.microsoft\.com/'
    r'[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}/'
    r'[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}(?=")'
)


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
