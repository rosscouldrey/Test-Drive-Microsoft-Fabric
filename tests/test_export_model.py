import base64
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch

from fabric_test_drive.api import DeploymentError, Fabric
from fabric_test_drive.export_model import export_model, main
from test_deployment import TENANT, WORKSPACE, NOTEBOOK, response


MODEL = NOTEBOOK


def part(path="definition/model.tmdl", content=b"model Model\n"):
    return {
        "path": path, "payloadType": "InlineBase64",
        "payload": base64.b64encode(content).decode(),
    }


class ExportModelTests(unittest.TestCase):
    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.output = Path(directory.name) / "Northwind.SemanticModel"

    def test_exports_decoded_definition_files_without_changing_remote_items(self):
        api = Mock()
        api.request.return_value = response({"definition": {"parts": [
            part(), part("definition.pbism", b'{"version":"4.0"}\n'),
        ]}})
        export_model(api, WORKSPACE, MODEL, self.output)
        self.assertEqual((self.output / "definition/model.tmdl").read_bytes(), b"model Model\n")
        self.assertEqual(
            (self.output / "definition.pbism").read_bytes(), b'{"version":"4.0"}\n',
        )
        api.request.assert_called_once_with(
            "POST", f"workspaces/{WORKSPACE}/semanticModels/{MODEL}/getDefinition?format=TMDL"
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
            response({"definition": {"parts": [part()]}}),
        ])
        export_model(api, WORKSPACE, MODEL, self.output)
        self.assertTrue((self.output / "definition/model.tmdl").is_file())
        self.assertTrue(api.session.request.call_args.args[1].endswith("/result"))

    def test_existing_directory_is_not_overwritten(self):
        self.output.mkdir()
        existing = self.output / "definition.pbism"
        existing.write_text("original")
        api = Mock()
        with self.assertRaisesRegex(DeploymentError, "already exists"):
            export_model(api, WORKSPACE, MODEL, self.output)
        api.request.assert_not_called()
        self.assertEqual(existing.read_text(), "original")

    def test_invalid_definition_is_rejected_before_writing_files(self):
        for parts in [
            [],
            [None],
            [part("../outside.tmdl")],
            [part("/outside.tmdl")],
            [part("definition\\outside.tmdl")],
            [part(), part()],
            [part("definition"), part()],
            [part(), {**part("definition.pbism"), "payload": "not base64!"}],
            [{**part(), "payloadType": "Unknown"}],
        ]:
            with self.subTest(parts=parts):
                api = Mock()
                api.request.return_value = response({"definition": {"parts": parts}})
                with self.assertRaises(DeploymentError):
                    export_model(api, WORKSPACE, MODEL, self.output)
                self.assertFalse(self.output.exists())

    def test_export_failure_leaves_no_output(self):
        api = Mock()
        api.request.side_effect = DeploymentError("HTTP 403")
        with self.assertRaisesRegex(DeploymentError, "HTTP 403"):
            export_model(api, WORKSPACE, MODEL, self.output)
        self.assertFalse(self.output.exists())

    @patch("fabric_test_drive.export_model.export_model")
    @patch("fabric_test_drive.export_model.Fabric")
    @patch("fabric_test_drive.export_model.load")
    def test_command_reuses_state_and_cached_signin(self, load, fabric, export):
        load.return_value = {"tenant_id": TENANT, "workspace_id": WORKSPACE}
        self.assertEqual(main(["--model", MODEL]), 0)
        fabric.assert_called_once_with(TENANT)
        export.assert_called_once_with(
            fabric.return_value, WORKSPACE, MODEL, Path("templates/Northwind.SemanticModel"),
        )

    @patch("fabric_test_drive.export_model.Fabric")
    @patch("fabric_test_drive.export_model.load")
    def test_missing_state_requires_explicit_tenant_and_workspace(self, load, fabric):
        load.return_value = None
        self.assertEqual(main(["--model", MODEL]), 1)
        fabric.assert_not_called()

    @patch("fabric_test_drive.export_model.export_model")
    @patch("fabric_test_drive.export_model.Fabric")
    @patch("fabric_test_drive.export_model.load")
    def test_explicit_ids_do_not_depend_on_local_state(self, load, fabric, export):
        self.assertEqual(main([
            "--tenant", TENANT, "--workspace", WORKSPACE, "--model", MODEL,
            "--output", str(self.output),
        ]), 0)
        load.assert_not_called()
        export.assert_called_once_with(fabric.return_value, WORKSPACE, MODEL, self.output)


if __name__ == "__main__":
    unittest.main()
