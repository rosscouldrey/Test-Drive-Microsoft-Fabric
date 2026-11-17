import unittest
from unittest.mock import Mock, patch

from fabric_test_drive import app, cli
from fabric_test_drive.api import DeploymentError

TENANT = "11111111-1111-1111-1111-111111111111"
WORKSPACE = "22222222-2222-2222-2222-222222222222"
MODEL = "33333333-3333-3333-3333-333333333333"
ITEM = "44444444-4444-4444-4444-444444444444"


def state():
    return {
        "tenant_id": TENANT, "workspace_id": WORKSPACE, "semantic_model_id": MODEL,
        "workspace_name": "Test workspace", "verified": True,
    }


def preview():
    return {
        "status": "dry-run", "blocked": False,
        "plan": {"workspaceId": WORKSPACE, "workspaceName": "Test workspace", "itemName": app.APP_NAME},
    }


def result():
    return {
        "status": "success",
        "deployment": {
            "fabricWorkspaceId": WORKSPACE, "itemName": app.APP_NAME, "rayfinItemId": ITEM,
            "hostingUrl": "https://example.test",
        },
        "generate": [{"name": "northwind-model", "status": "skipped", "skipReason": "non-graphql-connector"}],
    }


class AppTests(unittest.TestCase):
    def setUp(self):
        self.api = Mock()
        self.api.list.return_value = []
        self.api.request.return_value.json.return_value = {
            "id": ITEM, "type": "AppBackend", "displayName": app.APP_NAME,
        }
        self.save = Mock()

    @patch("fabric_test_drive.app.STAGING")
    @patch("fabric_test_drive.app.prepare")
    @patch("fabric_test_drive.app.command")
    @patch("fabric_test_drive.app.json_command")
    @patch("builtins.input", return_value="deploy app")
    def test_confirmed_target_is_bound_and_recorded(self, prompt, json_command, command, prepare, staging):
        staging.joinpath.return_value.exists.return_value = False
        json_command.side_effect = [preview(), result()]
        deployment = state()
        app.deploy_app(self.api, deployment, self.save)
        connector = command.call_args_list[1].args[0]
        self.assertIn(WORKSPACE, connector)
        self.assertIn(MODEL, connector)
        self.assertEqual(self.save.call_count, 2)
        self.assertEqual(deployment["app_id"], ITEM)
        self.assertNotIn("pending_app", deployment)

    @patch("fabric_test_drive.app.STAGING")
    @patch("fabric_test_drive.app.prepare")
    @patch("fabric_test_drive.app.command")
    @patch("fabric_test_drive.app.json_command")
    @patch("builtins.input", return_value="deploy app")
    def test_failure_after_submission_keeps_pending_marker(self, prompt, json_command, command, prepare, staging):
        staging.joinpath.return_value.exists.return_value = False
        bad = result()
        bad["generate"] = [{"name": "northwind-model", "status": "error"}]
        json_command.side_effect = [preview(), bad]
        deployment = state()
        with self.assertRaises(DeploymentError):
            app.deploy_app(self.api, deployment, self.save)
        self.assertTrue(deployment["pending_app"])
        self.assertNotIn("app_id", deployment)
        with self.assertRaisesRegex(DeploymentError, "unresolved"):
            app.deploy_app(self.api, deployment, self.save)

    @patch("fabric_test_drive.app.STAGING")
    @patch("fabric_test_drive.app.prepare")
    @patch("fabric_test_drive.app.command")
    @patch("fabric_test_drive.app.json_command")
    def test_wrong_preview_never_deploys(self, json_command, command, prepare, staging):
        staging.joinpath.return_value.exists.return_value = False
        bad = preview()
        bad["plan"]["workspaceId"] = ITEM
        json_command.return_value = bad
        with self.assertRaisesRegex(DeploymentError, "preview"):
            app.deploy_app(self.api, state(), self.save)
        self.assertEqual(json_command.call_count, 1)
        self.save.assert_not_called()

    def test_unrecorded_name_collision_never_deploys(self):
        self.api.list.return_value = [{"displayName": app.APP_NAME, "id": ITEM}]
        with self.assertRaisesRegex(DeploymentError, "unrecorded"):
            app.deploy_app(self.api, state(), self.save)
        self.save.assert_not_called()

    @patch("fabric_test_drive.cli.deploy_app")
    @patch("fabric_test_drive.cli.ensure_item")
    @patch("fabric_test_drive.cli.owned_workspace")
    @patch("fabric_test_drive.cli.preflight")
    @patch("fabric_test_drive.cli.Fabric")
    @patch("fabric_test_drive.cli.load")
    def test_completed_deployment_skips_ingestion(self, load, fabric, preflight, owned, ensure, deploy_app):
        deployment = state() | {
            "version": 1, "capacity_id": ITEM, "report_id": ITEM,
        }
        load.return_value = deployment
        self.assertEqual(cli.main(["deploy", "--skip-login", "--with-app"]), 0)
        preflight.assert_not_called()
        self.assertEqual(ensure.call_count, 2)
        deploy_app.assert_called_once_with(fabric.return_value, deployment, cli.save)


if __name__ == "__main__":
    unittest.main()
