import base64
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from fabric_test_drive.api import DeploymentError
from fabric_test_drive.model import MODEL_TEMPLATE, model_definition
from test_deployment import LAKEHOUSE, WORKSPACE


class ModelDefinitionTests(unittest.TestCase):
    def test_exported_template_binds_to_new_workspace_and_lakehouse(self):
        definition = model_definition(WORKSPACE, LAKEHOUSE)
        parts = {p["path"]: base64.b64decode(p["payload"]) for p in definition["parts"]}
        self.assertEqual(definition["format"], "TMDL")
        self.assertIn(f"https://onelake.dfs.fabric.microsoft.com/{WORKSPACE}/{LAKEHOUSE}".encode(),
                      parts["definition/expressions.tmdl"])
        self.assertIn(b"measure 'Total Sales'", parts["definition/tables/_measures.tmdl"])
        self.assertNotIn(".platform", parts)
        self.assertNotEqual(parts["definition/expressions.tmdl"],
                            (MODEL_TEMPLATE / "definition/expressions.tmdl").read_bytes())

    def test_missing_or_unexpected_sources_fail_closed(self):
        with TemporaryDirectory() as tmp:
            template = Path(tmp)
            (template / "definition").mkdir()
            (template / "definition.pbism").write_text("{}")
            expression = template / "definition/expressions.tmdl"
            with patch("fabric_test_drive.model.MODEL_TEMPLATE", template):
                for content in (
                    "no source",
                    "https://onelake.dfs.fabric.microsoft.com/bad/source",
                    "https://onelake.dfs.fabric.microsoft.com/" + WORKSPACE + "/" + LAKEHOUSE
                    + '"\nhttps://onelake.dfs.fabric.microsoft.com/extra/source"',
                    "https://onelake.dfs.fabric.microsoft.com/" + WORKSPACE + "/" + LAKEHOUSE
                    + '/Tables/gold"',
                ):
                    with self.subTest(content=content):
                        expression.write_text(content)
                        with self.assertRaisesRegex(DeploymentError, "exactly one"):
                            model_definition(WORKSPACE, LAKEHOUSE)
                expression.write_text(
                    "https://onelake.dfs.fabric.microsoft.com/" + WORKSPACE + "/" + LAKEHOUSE + '"'
                )
                (template / "definition/other.tmdl").write_text(
                    "https://onelake.dfs.fabric.microsoft.com/elsewhere"
                )
                with self.assertRaisesRegex(DeploymentError, "Unexpected OneLake"):
                    model_definition(WORKSPACE, LAKEHOUSE)
