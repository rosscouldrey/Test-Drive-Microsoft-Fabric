import base64
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch

from fabric_test_drive.api import DeploymentError, Fabric
from fabric_test_drive.export_report import export_report, main
from test_deployment import TENANT, WORKSPACE, NOTEBOOK, response


REPORT = NOTEBOOK


def part(path, content=b"{}"):
    return {
        "path": path, "payloadType": "InlineBase64",
        "payload": base64.b64encode(content).decode(),
    }


class ExportReportTests(unittest.TestCase):
    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.output = Path(directory.name) / "Northwind.Report"

    def test_exports_pbir_files_without_mutating_the_report(self):
        parts = [
            part(".platform", b'{"metadata":{"type":"Report"}}'),
            part("definition.pbir", b'{"datasetReference":{"byConnection":{}}}'),
            part("definition/report.json", b'{"sections":[]}'),
            part("definition/pages/page1/page.json", b'{"name":"page1"}'),
        ]
        api = Mock()
        api.request.return_value = response({"definition": {"parts": parts}})
        export_report(api, WORKSPACE, REPORT, self.output)
        self.assertEqual(
            (self.output / "definition.pbir").read_bytes(),
            b'{"datasetReference":{"byConnection":{}}}',
        )
        self.assertEqual(
            (self.output / "definition/pages/page1/page.json").read_bytes(),
            b'{"name":"page1"}',
        )
        api.request.assert_called_once_with(
            "POST", f"workspaces/{WORKSPACE}/reports/{REPORT}/getDefinition",
        )
        api.wait_operation.assert_not_called()

    @patch("fabric_test_drive.api.time.sleep")
    def test_async_export_polls_operation_and_reads_result(self, sleep):
        api = Fabric(TENANT)
        api.token = Mock(return_value="test-token")
        api.session.request = Mock(side_effect=[
            response(status=202, headers={
                "x-ms-operation-id": "99999999-9999-9999-9999-999999999999",
            }),
            response({"status": "Succeeded"}),
            response({"definition": {"parts": [part("definition.pbir")]}}),
        ])
        export_report(api, WORKSPACE, REPORT, self.output)
        self.assertTrue((self.output / "definition.pbir").is_file())
        self.assertTrue(api.session.request.call_args.args[1].endswith("/result"))

    def test_supports_pbir_legacy_entry_point(self):
        api = Mock()
        api.request.return_value = response({"definition": {
            "parts": [part("report.json", b'{"sections":[]}')],
        }})
        export_report(api, WORKSPACE, REPORT, self.output)
        self.assertTrue((self.output / "report.json").is_file())

    def test_existing_directory_is_not_overwritten(self):
        self.output.mkdir()
        existing = self.output / "definition.pbir"
        existing.write_text("original")
        api = Mock()
        with self.assertRaisesRegex(DeploymentError, "already exists"):
            export_report(api, WORKSPACE, REPORT, self.output)
        api.request.assert_not_called()
        self.assertEqual(existing.read_text(), "original")

    def test_invalid_definition_is_rejected_before_writing_files(self):
        cases = [
            [],
            [None],
            [part("../outside.json")],
            [part("/outside.json")],
            [part("definition\\outside.json")],
            [part("definition.pbir"), part("definition.pbir")],
            [part("definition.pbir"), part("definition.pbir/child.json")],
            [part("definition.pbir"), {**part("page.json"), "payload": "not base64!"}],
            [{**part("definition.pbir"), "payloadType": "Unknown"}],
            [part("unrecognized.json")],
        ]
        for index, parts in enumerate(cases):
            with self.subTest(parts=parts):
                output = self.output / str(index)
                api = Mock()
                api.request.return_value = response({"definition": {"parts": parts}})
                with self.assertRaises(DeploymentError):
                    export_report(api, WORKSPACE, REPORT, output)
                self.assertFalse(output.exists())

    def test_export_failure_leaves_no_output(self):
        api = Mock()
        api.request.side_effect = DeploymentError("HTTP 403")
        with self.assertRaisesRegex(DeploymentError, "HTTP 403"):
            export_report(api, WORKSPACE, REPORT, self.output)
        self.assertFalse(self.output.exists())

    @patch("fabric_test_drive.export_report.export_report")
    @patch("fabric_test_drive.export_report.Fabric")
    @patch("fabric_test_drive.export_report.load")
    def test_command_reuses_state_and_cached_signin(self, load, fabric, export):
        load.return_value = {"tenant_id": TENANT, "workspace_id": WORKSPACE}
        self.assertEqual(main(["--report", REPORT]), 0)
        fabric.assert_called_once_with(TENANT)
        export.assert_called_once_with(
            fabric.return_value, WORKSPACE, REPORT, Path("templates/Northwind.Report"),
        )

    @patch("fabric_test_drive.export_report.Fabric")
    @patch("fabric_test_drive.export_report.load")
    def test_missing_state_requires_explicit_tenant_and_workspace(self, load, fabric):
        load.return_value = None
        self.assertEqual(main(["--report", REPORT]), 1)
        fabric.assert_not_called()

    @patch("fabric_test_drive.export_report.export_report")
    @patch("fabric_test_drive.export_report.Fabric")
    @patch("fabric_test_drive.export_report.load")
    def test_explicit_ids_do_not_depend_on_local_state(self, load, fabric, export):
        self.assertEqual(main([
            "--tenant", TENANT, "--workspace", WORKSPACE, "--report", REPORT,
            "--output", str(self.output),
        ]), 0)
        load.assert_not_called()
        export.assert_called_once_with(fabric.return_value, WORKSPACE, REPORT, self.output)


if __name__ == "__main__":
    unittest.main()
