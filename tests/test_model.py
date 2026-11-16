import base64
import copy
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch

import requests

from fabric_test_drive.api import DeploymentError, PowerBI
from fabric_test_drive.model import MODEL_TEMPLATE, model_definition, verify_model
from test_deployment import LAKEHOUSE, WORKSPACE, TENANT, NOTEBOOK, response


REFRESH = "99999999-9999-9999-9999-999999999999"
PATH = f"groups/{WORKSPACE}/datasets/{NOTEBOOK}"
READY = {"targetStorageMode": "PremiumFiles"}
QUERY = {"results": [{"tables": [{"rows": [{"[Sales]": 123.45, "[Orders]": 12}]}]}]}


def accepted(location=None):
    return response(status=202, headers={
        "Location": location or f"https://api.powerbi.com/v1.0/myorg/{PATH}/refreshes/{REFRESH}",
    })


class ModelReadinessTests(unittest.TestCase):
    def setUp(self):
        self.api = PowerBI(TENANT)
        self.api.token = Mock(return_value="powerbi-token")
        self.api.session.request = Mock()
        self.state = {
            "workspace_id": WORKSPACE, "semantic_model_id": NOTEBOOK,
            "completed": ["verification"],
        }
        self.saved = []
        self.save = lambda state: self.saved.append(copy.deepcopy(state))
        api_patch = patch("fabric_test_drive.model.PowerBI", return_value=self.api)
        api_patch.start()
        self.addCleanup(api_patch.stop)
        sleep_patch = patch("fabric_test_drive.api.time.sleep")
        self.sleep = sleep_patch.start()
        self.addCleanup(sleep_patch.stop)

    def test_waits_for_storage_and_refresh_before_querying_measures(self):
        self.api.session.request.side_effect = [
            response({"targetStorageMode": "Abf"}), response(),
            response({"targetStorageMode": "Abf"}), response(READY),
            accepted(), response({"status": "Unknown"}, status=202),
            response({"status": "Completed"}), response(QUERY),
        ]
        verify_model(TENANT, self.state, self.save)
        calls = self.api.session.request.call_args_list
        self.assertEqual([c.args[0] for c in calls],
                         ["GET", "PATCH", "GET", "GET", "POST", "GET", "GET", "POST"])
        self.assertEqual(calls[4].kwargs["json"], {
            "type": "full", "commitMode": "transactional", "retryCount": 2, "timeout": "00:05:00",
        })
        self.assertIn("model_refresh", self.state["completed"])
        self.assertIn("model_query", self.state["completed"])
        self.assertEqual(self.saved[0]["pending_job"], "model_refresh")
        self.assertEqual(self.saved[1]["model_refresh_id"], REFRESH)
        self.assertNotIn("pending_job", self.saved[1])
        self.assertEqual(self.sleep.call_count, 3)

    def test_resumes_same_active_refresh_instead_of_submitting_another(self):
        self.state["model_refresh_id"] = REFRESH
        self.api.session.request.side_effect = [
            response(READY), response({"status": "InProgress"}, status=202),
            response({"status": "Completed"}), response(QUERY),
        ]
        verify_model(TENANT, self.state, self.save)
        posts = [c.args[1] for c in self.api.session.request.call_args_list if c.args[0] == "POST"]
        self.assertEqual(posts, [self.api.url(PATH + "/executeQueries")])

    def test_terminal_failure_is_explicit_and_next_invocation_can_retry(self):
        self.api.session.request.side_effect = [
            response(READY), accepted(),
            response({"status": "Failed", "serviceExceptionJson": "access denied"}),
        ]
        with self.assertRaisesRegex(DeploymentError, "access denied"):
            verify_model(TENANT, self.state, self.save)
        self.assertNotIn("model_refresh", self.state["completed"])
        self.assertEqual(self.state["model_refresh_id"], REFRESH)
        self.api.session.request.side_effect = [
            response(READY), response({"status": "Failed"}), accepted(),
            response({"status": "Completed"}), response(QUERY),
        ]
        verify_model(TENANT, self.state, self.save)
        self.assertIn("model_query", self.state["completed"])

    def test_ambiguous_submission_is_not_repeated_and_preserves_pending_marker(self):
        self.api.session.request.side_effect = [response(READY), requests.ConnectionError("lost")]
        with self.assertRaisesRegex(DeploymentError, "network failure"):
            verify_model(TENANT, self.state, self.save)
        self.assertEqual(self.state["pending_job"], "model_refresh")
        self.assertEqual(self.api.session.request.call_count, 2)

    def test_untrusted_or_missing_polling_url_leaves_pending_marker(self):
        for result in (
            response(status=202),
            accepted("https://example.com/v1.0/myorg/refreshes/" + REFRESH),
            accepted(self.api.url(f"groups/{WORKSPACE}/datasets/{LAKEHOUSE}/refreshes/{REFRESH}")),
        ):
            with self.subTest(headers=result.headers):
                self.api.session.request.side_effect = [response(READY), result]
                with self.assertRaises(DeploymentError):
                    verify_model(TENANT, self.state, self.save)
                self.assertEqual(self.state["pending_job"], "model_refresh")

    def test_timeout_keeps_refresh_id_for_resume(self):
        self.state["model_refresh_id"] = REFRESH
        self.api.timeout = 0
        self.api.session.request.side_effect = [
            response(READY), response({"status": "Unknown"}, status=202),
        ]
        with self.assertRaisesRegex(DeploymentError, "timed out"):
            verify_model(TENANT, self.state, self.save)
        self.assertEqual(self.state["model_refresh_id"], REFRESH)
        self.assertNotIn("model_refresh", self.state["completed"])

    def test_query_errors_or_nonpositive_results_never_complete_verification(self):
        self.state["completed"].append("model_refresh")
        for data in (
            {"results": [{"error": {"message": "DAX failure"}}]},
            {"results": [{"tables": [{"rows": [{"[Sales]": 0, "[Orders]": 12}]}]}]},
            {"results": [{"tables": [{"rows": [{"[Sales]": 1, "[Orders]": None}]}]}]},
            {"results": [{"tables": [{"rows": [{"[Sales]": 1, "[Orders]": True}]}]}]},
            {"results": [{"tables": [{"rows": []}]}]},
        ):
            with self.subTest(data=data):
                self.api.session.request.side_effect = [response(READY), response(data)]
                with self.assertRaisesRegex(DeploymentError, "positive Sales and Orders"):
                    verify_model(TENANT, self.state, self.save)
                self.assertNotIn("model_query", self.state["completed"])

    def test_query_permission_error_does_not_repeat_completed_refresh(self):
        self.state["completed"].append("model_refresh")
        self.api.session.request.side_effect = [response(READY), response(status=403)]
        with self.assertRaisesRegex(DeploymentError, "HTTP 403"):
            verify_model(TENANT, self.state, self.save)
        self.assertIn("model_refresh", self.state["completed"])
        self.assertNotIn("model_query", self.state["completed"])

    @patch("fabric_test_drive.api.azure", return_value='{"accessToken":"powerbi-token"}')
    def test_powerbi_uses_its_own_token_audience_and_rejects_other_hosts(self, azure):
        api = PowerBI(TENANT)
        self.assertEqual(api.token(), "powerbi-token")
        self.assertIn("https://analysis.windows.net/powerbi/api", azure.call_args.args)
        for url in (
            "https://api.fabric.microsoft.com/v1/workspaces",
            "https://example.com/v1.0/myorg/test",
            "http://api.powerbi.com/v1.0/myorg/test",
        ):
            with self.subTest(url=url), self.assertRaisesRegex(DeploymentError, "unexpected URL"):
                api.url(url)


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
