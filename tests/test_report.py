import base64
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from fabric_test_drive.api import DeploymentError
from fabric_test_drive.report import REPORT_TEMPLATE, report_definition, verify_report
from test_deployment import MODEL, REPORT, TENANT, WORKSPACE, response


class ReportTests(unittest.TestCase):
    def test_authored_report_rebinds_without_changing_visuals_or_template(self):
        source = (REPORT_TEMPLATE / "definition.pbir").read_bytes()
        parts = {
            p["path"]: base64.b64decode(p["payload"])
            for p in report_definition(MODEL)["parts"]
        }
        definition = json.loads(parts["definition.pbir"])
        self.assertEqual(definition["datasetReference"], {
            "byConnection": {"connectionString": f"semanticmodelid={MODEL}"},
        })
        self.assertNotIn(".platform", parts)
        for path, content in parts.items():
            if path != "definition.pbir":
                self.assertEqual(content, (REPORT_TEMPLATE / path).read_bytes())
        self.assertEqual((REPORT_TEMPLATE / "definition.pbir").read_bytes(), source)
        self.assertTrue(any("visuals/" in path for path in parts))

    def test_by_path_binding_is_replaced_in_legacy_report_too(self):
        with TemporaryDirectory() as tmp:
            template = Path(tmp)
            (template / "definition.pbir").write_text(json.dumps({
                "version": "4.0", "datasetReference": {"byPath": {"path": "../OldModel"}},
            }))
            (template / "report.json").write_text("{}")
            with patch("fabric_test_drive.report.REPORT_TEMPLATE", template):
                parts = report_definition(MODEL)["parts"]
            definition = json.loads(base64.b64decode(
                next(p["payload"] for p in parts if p["path"] == "definition.pbir")
            ))
            self.assertNotIn("byPath", definition["datasetReference"])
            self.assertEqual(definition["version"], "4.0")

    def test_incomplete_report_is_rejected(self):
        with TemporaryDirectory() as tmp:
            with patch("fabric_test_drive.report.REPORT_TEMPLATE", Path(tmp)):
                with self.assertRaisesRegex(DeploymentError, "incomplete"):
                    report_definition(MODEL)

    @patch("fabric_test_drive.report.PowerBI")
    def test_checks_live_report_dataset_id(self, powerbi):
        state = {"workspace_id": WORKSPACE, "semantic_model_id": MODEL, "report_id": REPORT}
        api = powerbi.return_value
        api.request.return_value = response({"datasetId": MODEL})
        verify_report(TENANT, state)
        api.request.assert_called_once_with("GET", f"groups/{WORKSPACE}/reports/{REPORT}")
        for dataset in (None, WORKSPACE):
            with self.subTest(dataset=dataset):
                api.request.return_value = response({"datasetId": dataset})
                with self.assertRaisesRegex(DeploymentError, "not bound"):
                    verify_report(TENANT, state)
