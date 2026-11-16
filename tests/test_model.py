import base64
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import requests

from fabric_test_drive.api import DeploymentError
from fabric_test_drive.model import MODEL_TEMPLATE, ensure_large_storage, model_definition
from test_deployment import LAKEHOUSE, WORKSPACE, TENANT, NOTEBOOK, response


@patch("fabric_test_drive.model.azure", return_value='{"accessToken":"test-token"}')
@patch("fabric_test_drive.model.requests.request")
class StorageTests(unittest.TestCase):
    def test_small_storage_is_changed_and_confirmed(self, request, azure):
        request.side_effect = [
            response({"targetStorageMode": "Abf"}), response(status=200),
            response({"targetStorageMode": "PremiumFiles"}),
        ]
        ensure_large_storage(TENANT, WORKSPACE, NOTEBOOK)
        self.assertEqual([c.args[0] for c in request.call_args_list], ["GET", "PATCH", "GET"])
        self.assertEqual(request.call_args_list[1].kwargs["json"],
                         {"targetStorageMode": "PremiumFiles"})
        self.assertFalse(request.call_args.kwargs["allow_redirects"])
        self.assertIn(TENANT, azure.call_args.args)

    def test_existing_large_storage_is_not_changed(self, request, azure):
        request.return_value = response({"targetStorageMode": "PremiumFiles"})
        ensure_large_storage(TENANT, WORKSPACE, NOTEBOOK)
        request.assert_called_once()

    def test_unconfirmed_conversion_is_not_success(self, request, azure):
        request.side_effect = [
            response({"targetStorageMode": "Abf"}), response(status=200),
            response({"targetStorageMode": "Abf"}),
        ]
        with self.assertRaisesRegex(DeploymentError, "not yet confirmed"):
            ensure_large_storage(TENANT, WORKSPACE, NOTEBOOK)

    def test_permission_and_network_errors_are_reported(self, request, azure):
        request.return_value = response({"error": "denied"}, status=403)
        with self.assertRaisesRegex(DeploymentError, "HTTP 403"):
            ensure_large_storage(TENANT, WORKSPACE, NOTEBOOK)
        request.side_effect = requests.ConnectionError("lost connection")
        with self.assertRaisesRegex(DeploymentError, "request failed"):
            ensure_large_storage(TENANT, WORKSPACE, NOTEBOOK)


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
