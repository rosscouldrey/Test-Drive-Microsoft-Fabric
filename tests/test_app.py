import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
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
        self.api.tenant = TENANT
        self.api.token.return_value = "test-token"

    @patch("fabric_test_drive.app.subprocess.run")
    def test_rayfin_reuses_azure_token_without_global_environment_changes(self, run):
        run.return_value = Mock(returncode=0, stdout="output")
        with patch.dict(os.environ, {"RAYFIN_TOKEN": "unrelated-token"}):
            app.command(["rayfin", "up"], api=self.api)
            self.assertEqual(os.environ["RAYFIN_TOKEN"], "unrelated-token")
            self.assertEqual(run.call_args.kwargs["env"]["RAYFIN_TOKEN"], "test-token")
            self.assertEqual(run.call_args.kwargs["env"]["RAYFIN_TENANT_ID"], TENANT)
            app.command(["npm", "run", "typecheck"])
            self.assertNotIn("RAYFIN_TOKEN", run.call_args.kwargs["env"])
        self.api.token.assert_called_once_with()

    @patch("fabric_test_drive.app.command")
    def test_json_accepts_only_known_leading_authentication_notice(self, command):
        notice = "[rayfin] using ambient token from RAYFIN_TOKEN\n"
        for prefix in ("", notice, notice * 2):
            with self.subTest(prefix=prefix):
                command.return_value = prefix + json.dumps(preview())
                self.assertEqual(app.json_command(["rayfin", "up"]), preview())
        for output in (
            "unknown warning\n" + json.dumps(preview()),
            notice + "{broken",
            notice + json.dumps(preview()) + "\nextra output",
            notice + "[]",
        ):
            with self.subTest(output=output):
                command.return_value = output
                with self.assertRaises(DeploymentError):
                    app.json_command(["rayfin", "up"])

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
        connector = command.call_args_list[0].args[0]
        self.assertIn(WORKSPACE, connector)
        self.assertIn(MODEL, connector)
        self.assertEqual(self.save.call_count, 2)
        self.assertEqual(deployment["app_id"], ITEM)
        self.assertNotIn("pending_app", deployment)
        self.assertEqual(command.call_args_list[0].kwargs["api"], self.api)
        self.assertEqual(json_command.call_args_list[0].kwargs["api"], self.api)
        self.assertEqual(json_command.call_args_list[1].kwargs["api"], self.api)

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

    @patch("fabric_test_drive.cli.check_prerequisites")
    @patch("fabric_test_drive.cli.deploy_app")
    @patch("fabric_test_drive.cli.ensure_item")
    @patch("fabric_test_drive.cli.owned_workspace")
    @patch("fabric_test_drive.cli.preflight")
    @patch("fabric_test_drive.cli.Fabric")
    @patch("fabric_test_drive.cli.load")
    def test_completed_deployment_skips_ingestion(
        self, load, fabric, preflight, owned, ensure, deploy_app, prerequisites,
    ):
        deployment = state() | {
            "version": 1, "capacity_id": ITEM, "report_id": ITEM, "app_requested": True,
        }
        load.return_value = deployment
        for flags in (["--with-app"], []):
            with self.subTest(flags=flags):
                deploy_app.reset_mock()
                prerequisites.reset_mock()
                ensure.reset_mock()
                with patch("fabric_test_drive.cli.save") as save:
                    self.assertEqual(cli.main(["deploy", "--skip-login"] + flags), 0)
                    deploy_app.assert_called_once_with(fabric.return_value, deployment, save)
                prerequisites.assert_called_once_with()
                self.assertEqual(ensure.call_count, 2)
        preflight.assert_not_called()
        self.assertTrue(deployment["app_requested"])

    @patch("fabric_test_drive.cli.check_prerequisites")
    @patch("fabric_test_drive.cli.deploy_app")
    @patch("fabric_test_drive.cli.Fabric")
    @patch("fabric_test_drive.cli.preflight")
    @patch("fabric_test_drive.cli.load")
    def test_preflight_does_not_deploy_saved_app_choice(self, load, preflight, fabric, deploy_app, prerequisites):
        load.return_value = state() | {"capacity_id": ITEM, "app_requested": True}
        preflight.return_value = ([{"id": ITEM, "state": "Active"}], [])
        self.assertEqual(cli.main(["preflight", "--skip-login"]), 0)
        deploy_app.assert_not_called()
        prerequisites.assert_not_called()

    @patch("fabric_test_drive.cli.sys.stdin.isatty", return_value=True)
    @patch("fabric_test_drive.cli.login")
    @patch("fabric_test_drive.cli.check_prerequisites")
    @patch("fabric_test_drive.cli.deploy")
    @patch("fabric_test_drive.cli.deploy_app")
    @patch("fabric_test_drive.cli.Fabric")
    @patch("fabric_test_drive.cli.preflight")
    @patch("fabric_test_drive.cli.load", return_value=None)
    def test_new_deployment_prompt_and_flags(
        self, load, preflight, fabric, deploy_app, deploy, prerequisites, login, isatty,
    ):
        preflight.return_value = ([{"id": ITEM, "state": "Active"}], [])
        base_args = ["deploy", "--tenant", TENANT, "--capacity", ITEM, "--name", "App Test"]
        for interactive, flags, answers, included in (
            (True, [], ["", "deploy"], False),
            (True, [], ["yes", "deploy"], True),
            (True, ["--with-app"], ["deploy"], True),
            (True, ["--without-app"], ["deploy"], False),
            (False, [], ["deploy"], False),
        ):
            with self.subTest(flags=flags, included=included):
                isatty.return_value = interactive
                deploy.reset_mock()
                deploy_app.reset_mock()
                prerequisites.reset_mock()
                with patch("builtins.input", side_effect=answers) as prompt:
                    self.assertEqual(cli.main(base_args + flags), 0)
                self.assertEqual(prompt.call_count, len(answers))
                self.assertEqual(deploy.call_args.kwargs["with_app"], included)
                self.assertEqual(deploy_app.call_count, int(included))
                self.assertEqual(prerequisites.call_count, int(included))

    @patch("fabric_test_drive.cli.load", return_value=None)
    @patch("fabric_test_drive.cli.login")
    @patch("fabric_test_drive.cli.check_prerequisites", side_effect=DeploymentError("Node missing"))
    def test_missing_app_prerequisite_stops_before_authentication(self, prerequisites, login, load):
        self.assertEqual(cli.main(["deploy", "--tenant", TENANT, "--with-app"]), 1)
        login.assert_not_called()

    @patch("fabric_test_drive.app.prepare")
    def test_existing_recorded_app_is_reused_without_redeployment(self, prepare):
        self.api.list.return_value = [{"id": ITEM, "displayName": app.APP_NAME, "type": "AppBackend"}]
        deployment = state() | {"app_id": ITEM}
        app.deploy_app(self.api, deployment, self.save)
        prepare.assert_not_called()
        self.save.assert_not_called()

    def test_app_selection_survives_core_deployment_failure(self):
        self.api.create.side_effect = DeploymentError("workspace creation failed")
        with TemporaryDirectory() as directory:
            with patch.object(cli, "STATE", Path(directory) / "state.json"):
                with self.assertRaisesRegex(DeploymentError, "workspace creation failed"):
                    cli.deploy(self.api, None, TENANT, ITEM, "App Test", [], with_app=True)
                self.assertTrue(cli.load()["app_requested"])


if __name__ == "__main__":
    unittest.main()
