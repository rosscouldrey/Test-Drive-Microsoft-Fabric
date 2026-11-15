import base64
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch

import requests

from fabric_test_drive import cli
from fabric_test_drive.api import DeploymentError, Fabric
from fabric_test_drive.artifacts import ENTITIES, SOURCE, notebook, pipeline


TENANT = "11111111-1111-1111-1111-111111111111"
CAPACITY = "22222222-2222-2222-2222-222222222222"
WORKSPACE = "33333333-3333-3333-3333-333333333333"
LAKEHOUSE = "44444444-4444-4444-4444-444444444444"
CONNECTION = "55555555-5555-5555-5555-555555555555"
PIPELINE = "66666666-6666-6666-6666-666666666666"
NOTEBOOK = "77777777-7777-7777-7777-777777777777"
CONNECTION_TYPES = [{
    "type": "OData",
    "creationMethods": [{
        "name": "OData",
        "parameters": [{"name": "url", "dataType": "Text", "required": True}],
    }],
    "supportedCredentialTypes": ["Anonymous"],
    "supportedConnectionEncryptionTypes": ["Encrypted"],
}]


def response(data=None, status=200, headers=None):
    result = requests.Response()
    result.status_code = status
    result._content = json.dumps(data).encode()
    result.headers.update(headers or {})
    return result


class ApiTests(unittest.TestCase):
    def setUp(self):
        self.api = Fabric(TENANT)
        self.api.token = Mock(return_value="test-token")
        self.api.session.request = Mock()

    def test_pagination_cannot_forward_token_to_another_host(self):
        self.api.session.request.return_value = response({
            "value": [], "continuationUri": "https://example.com/v1/workspaces",
        })
        with self.assertRaisesRegex(DeploymentError, "unexpected URL"):
            self.api.list("workspaces")
        self.assertEqual(self.api.session.request.call_count, 1)

    def test_pagination_preserves_results_and_detects_cycles(self):
        self.api.session.request.side_effect = [
            response({"value": [{"id": "one"}], "continuationUri": "?page=2"}),
            response({"value": [{"id": "two"}]}),
        ]
        self.assertEqual(self.api.list("workspaces"), [{"id": "one"}, {"id": "two"}])
        self.api.session.request.side_effect = None
        self.api.session.request.return_value = response({
            "value": [], "continuationUri": "workspaces",
        })
        with self.assertRaisesRegex(DeploymentError, "repeated"):
            self.api.list("workspaces")

    @patch("fabric_test_drive.api.time.sleep")
    def test_reads_retry_throttling_without_automatic_write_retries(self, sleep):
        self.api.session.request.side_effect = [
            response(status=429, headers={"Retry-After": "2"}),
            response({"value": []}),
        ]
        self.assertEqual(self.api.list("workspaces"), [])
        sleep.assert_called_once_with(2)
        self.api.session.request.reset_mock()
        self.api.session.request.side_effect = None
        self.api.session.request.return_value = response(status=503)
        with self.assertRaisesRegex(DeploymentError, "HTTP 503"):
            self.api.create("workspaces", {"displayName": "demo"})
        self.api.session.request.assert_called_once()

    def test_network_failure_does_not_repeat_a_create(self):
        self.api.session.request.side_effect = requests.ConnectionError("lost response")
        with self.assertRaisesRegex(DeploymentError, "write may have completed"):
            self.api.create("workspaces", {})
        self.api.session.request.assert_called_once()

    @patch("fabric_test_drive.api.time.sleep")
    def test_create_waits_for_operation_result(self, sleep):
        location = "https://api.fabric.microsoft.com/v1/operations/operation"
        self.api.session.request.side_effect = [
            response(status=202, headers={"Location": location}),
            response({"status": "Running"}),
            response({"status": "Succeeded"}),
            response({"id": WORKSPACE}),
        ]
        self.assertEqual(self.api.create("workspaces", {}), {"id": WORKSPACE})
        self.assertEqual(
            self.api.session.request.call_args.args[1], location + "/result"
        )

    @patch("fabric_test_drive.api.time.sleep")
    def test_failed_operation_is_not_success(self, sleep):
        self.api.session.request.side_effect = [
            response(status=202, headers={"Location": "operations/operation"}),
            response({"status": "Failed", "error": {"message": "denied"}}),
        ]
        with self.assertRaisesRegex(DeploymentError, "Failed"):
            self.api.create("workspaces", {})

    @patch("fabric_test_drive.api.time.sleep")
    def test_job_location_is_recorded_before_polling_failure(self, sleep):
        location = "https://api.fabric.microsoft.com/v1/workspaces/ws/items/item/jobs/instances/job"
        self.api.session.request.side_effect = [
            response(status=202, headers={"Location": location}),
            response({"status": "Failed"}),
        ]
        remember = Mock()
        with self.assertRaisesRegex(DeploymentError, "Failed"):
            self.api.run("ws", "item", "Pipeline", remember)
        remember.assert_called_once_with(location)
        sleep.assert_called_once_with(5)

    def test_redirect_is_an_error_and_not_followed(self):
        self.api.session.request.return_value = response(
            status=302, headers={"Location": "https://example.com"}
        )
        with self.assertRaisesRegex(DeploymentError, "HTTP 302"):
            self.api.request("GET", "capacities")
        self.assertFalse(self.api.session.request.call_args.kwargs["allow_redirects"])

    @patch("fabric_test_drive.api.time.sleep")
    def test_excessive_retry_after_fails_without_unbounded_sleep(self, sleep):
        self.api.session.request.return_value = response(
            status=429, headers={"Retry-After": "86400"}
        )
        with self.assertRaisesRegex(DeploymentError, "timed out"):
            self.api.list("workspaces")
        sleep.assert_not_called()

    @patch("fabric_test_drive.api.time.sleep")
    def test_delete_waits_until_async_deletion_finishes(self, sleep):
        self.api.session.request.side_effect = [
            response(status=202, headers={"Location": "operations/delete"}),
            response({"status": "Running"}),
            response({"status": "Succeeded"}),
        ]
        self.api.delete(f"workspaces/{WORKSPACE}")
        self.assertEqual(self.api.session.request.call_count, 3)


class ArtifactTests(unittest.TestCase):
    def test_pipeline_binds_every_source_and_destination_to_this_deployment(self):
        content = pipeline(WORKSPACE, LAKEHOUSE, CONNECTION)
        decoded = json.loads(base64.b64decode(content["parts"][0]["payload"]))
        activities = decoded["properties"]["activities"]
        self.assertEqual(len(activities), len(ENTITIES))
        for index, (entity, table) in enumerate(ENTITIES.items()):
            activity = activities[index]
            source = activity["typeProperties"]["source"]["datasetSettings"]
            sink = activity["typeProperties"]["sink"]
            self.assertEqual(source["typeProperties"]["path"], entity)
            self.assertEqual(source["externalReferences"]["connection"], CONNECTION)
            dataset = sink["datasetSettings"]
            self.assertEqual(dataset["typeProperties"]["table"], table)
            target = dataset["linkedService"]["properties"]["typeProperties"]
            self.assertEqual(target["workspaceId"], WORKSPACE)
            self.assertEqual(target["artifactId"], LAKEHOUSE)
            self.assertEqual(sink["tableActionOption"], "Overwrite")
            if index:
                self.assertEqual(
                    activity["dependsOn"],
                    [{"activity": activities[index - 1]["name"],
                      "dependencyConditions": ["Succeeded"]}],
                )

    def test_notebook_attaches_to_new_lakehouse_and_is_valid_python(self):
        content = notebook(WORKSPACE, LAKEHOUSE)
        decoded = json.loads(base64.b64decode(content["parts"][0]["payload"]))
        attachment = decoded["metadata"]["dependencies"]["lakehouse"]
        self.assertEqual(attachment["default_lakehouse"], LAKEHOUSE)
        self.assertEqual(attachment["default_lakehouse_workspace_id"], WORKSPACE)
        compile("".join(decoded["cells"][0]["source"]), "<verification-notebook>", "exec")

    def test_connection_uses_discovered_method_and_public_anonymous_source(self):
        types = json.loads(json.dumps(CONNECTION_TYPES))
        types[0]["creationMethods"][0]["name"] = "AdvertisedODataMethod"
        result = cli.connection_body(types, "Northwind-test")
        self.assertEqual(result["connectionDetails"]["creationMethod"], "AdvertisedODataMethod")
        self.assertEqual(result["connectionDetails"]["parameters"][0]["value"], SOURCE)
        self.assertEqual(
            result["credentialDetails"]["credentials"], {"credentialType": "Anonymous"}
        )
        types[0]["creationMethods"][0]["parameters"].append({
            "name": "unsupportedRequiredField", "dataType": "Text", "required": True,
        })
        with self.assertRaisesRegex(DeploymentError, "feasibility blocker"):
            cli.connection_body(types, "Northwind-test")


class DeploymentTests(unittest.TestCase):
    def setUp(self):
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.state_path = Path(self.directory.name) / "state.json"
        self.state_patch = patch.object(cli, "STATE", self.state_path)
        self.state_patch.start()
        self.addCleanup(self.state_patch.stop)

    def state(self):
        return {
            "version": 1, "tenant_id": TENANT, "capacity_id": CAPACITY,
            "workspace_name": "Northwind Test", "workspace_id": WORKSPACE,
            "marker": "Fabric Test Drive deployment test",
            "connection_name": "Northwind-test", "completed": [], "jobs": {},
            "verified": False,
        }

    def test_resume_reuses_objects_and_completed_ingestion_after_notebook_failure(self):
        api = Mock()
        api.list.return_value = []
        api.create.side_effect = [
            {"id": WORKSPACE}, {"id": LAKEHOUSE}, {"id": CONNECTION},
            {"id": PIPELINE}, {"id": NOTEBOOK},
        ]
        items = {
            LAKEHOUSE: ("Lakehouse", "Northwind"),
            PIPELINE: ("DataPipeline", "Ingest Northwind"),
            NOTEBOOK: ("Notebook", "Verify Northwind"),
        }

        def get(method, path):
            state = cli.load()
            if path == f"workspaces/{WORKSPACE}":
                return response({"description": state["marker"], "capacityId": CAPACITY})
            if path == f"connections/{CONNECTION}":
                return response({"displayName": state["connection_name"]})
            if path.startswith("https:"):
                return response({"status": "Failed"})
            item = path.rsplit("/", 1)[-1]
            kind, name = items[item]
            return response({"id": item, "type": kind, "displayName": name})

        api.request.side_effect = get
        attempt = {"fail": True}

        def run(workspace, item, kind, on_started):
            on_started(f"https://api.fabric.microsoft.com/v1/jobs/{item}")
            if kind == "RunNotebook" and attempt["fail"]:
                raise DeploymentError("notebook failed")

        api.run.side_effect = run
        with self.assertRaisesRegex(DeploymentError, "notebook failed"):
            cli.deploy(api, None, TENANT, CAPACITY, "Northwind Test", CONNECTION_TYPES)
        state = cli.load()
        self.assertEqual(state["completed"], ["ingestion"])
        self.assertFalse(state["verified"])
        self.assertIn("verification", state["jobs"])
        attempt["fail"] = False
        cli.deploy(api, state, TENANT, CAPACITY, "Northwind Test", CONNECTION_TYPES)
        self.assertEqual(api.create.call_count, 5)
        self.assertEqual(api.run.call_count, 3)
        self.assertTrue(cli.load()["verified"])
        self.assertEqual(api.run.call_args.args[2], "RunNotebook")
        cli.deploy(api, cli.load(), TENANT, CAPACITY, "Northwind Test", CONNECTION_TYPES)
        self.assertEqual(api.create.call_count, 5)
        self.assertEqual(api.run.call_count, 5)

    def test_ownership_mismatch_prevents_cleanup(self):
        state = self.state()
        api = Mock()
        api.request.return_value = response({
            "description": "Unrelated workspace", "capacityId": CAPACITY,
        })
        with self.assertRaisesRegex(DeploymentError, "ownership"):
            cli.cleanup(api, state)
        api.delete.assert_not_called()

    def test_unresolved_create_cannot_be_repeated_or_silently_cleaned_up(self):
        state = self.state()
        api = Mock()
        api.create.side_effect = DeploymentError("response lost")
        with self.assertRaisesRegex(DeploymentError, "response lost"):
            cli.create_recorded(api, state, "lakehouse_id", "items", {})
        self.assertEqual(cli.load()["pending_create"], "lakehouse_id")
        with self.assertRaisesRegex(DeploymentError, "unresolved outcome"):
            cli.deploy(api, cli.load(), TENANT, CAPACITY, "Northwind Test", CONNECTION_TYPES)
        with self.assertRaisesRegex(DeploymentError, "unresolved create"):
            cli.cleanup(api, cli.load())
        self.assertEqual(api.create.call_count, 1)
        api.delete.assert_not_called()

    def test_lost_job_submission_blocks_duplicate_runs_and_cleanup(self):
        state = self.state()
        cli.save(state)
        api = Mock()
        api.run.side_effect = DeploymentError("response lost")
        with self.assertRaisesRegex(DeploymentError, "response lost"):
            cli.run_stage(api, state, "ingestion", PIPELINE, "Pipeline")
        self.assertEqual(cli.load()["pending_job"], "ingestion")
        with self.assertRaisesRegex(DeploymentError, "unresolved outcome"):
            cli.deploy(api, cli.load(), TENANT, CAPACITY, "Northwind Test", CONNECTION_TYPES)
        with self.assertRaisesRegex(DeploymentError, "unresolved job"):
            cli.cleanup(api, cli.load())
        self.assertEqual(api.run.call_count, 1)
        api.delete.assert_not_called()

    def test_confirmation_mismatch_prevents_cleanup(self):
        state = self.state()
        api = Mock()
        api.request.return_value = response({
            "description": state["marker"], "capacityId": CAPACITY,
        })
        with patch("builtins.input", return_value="no"):
            with self.assertRaisesRegex(DeploymentError, "cancelled"):
                cli.cleanup(api, state)
        api.delete.assert_not_called()

    def test_cleanup_deletes_only_recorded_resources_and_never_capacity(self):
        state = self.state()
        state["connection_id"] = CONNECTION
        cli.save(state)
        api = Mock()
        api.request.side_effect = [
            response({"description": state["marker"], "capacityId": CAPACITY}),
            response({"displayName": state["connection_name"]}),
        ]
        with patch("builtins.input", return_value=state["workspace_name"]):
            cli.cleanup(api, state)
        self.assertEqual(
            [call.args[0] for call in api.delete.call_args_list],
            [f"workspaces/{WORKSPACE}", f"connections/{CONNECTION}"],
        )
        self.assertFalse(self.state_path.exists())

    def test_running_job_prevents_cleanup(self):
        state = self.state()
        state["jobs"]["ingestion"] = "https://api.fabric.microsoft.com/v1/jobs/job"
        api = Mock()
        api.request.side_effect = [
            response({"description": state["marker"], "capacityId": CAPACITY}),
            response({"status": "InProgress"}),
        ]
        with patch("builtins.input", return_value=state["workspace_name"]):
            with self.assertRaisesRegex(DeploymentError, "still running"):
                cli.cleanup(api, state)
        api.delete.assert_not_called()

    @patch("fabric_test_drive.cli.login")
    def test_tenant_mismatch_stops_before_authentication(self, login):
        cli.save(self.state())
        self.assertEqual(cli.main(["deploy", "--tenant", CAPACITY]), 1)
        login.assert_not_called()

    @patch("fabric_test_drive.cli.requests.get")
    def test_source_outage_stops_preflight_without_cloud_writes(self, get):
        api = Mock()
        api.list.side_effect = [[], CONNECTION_TYPES]
        get.side_effect = requests.ConnectionError("offline")
        with self.assertRaisesRegex(DeploymentError, "No Fabric resources"):
            cli.preflight(api)
        api.create.assert_not_called()
        api.request.assert_not_called()

    @patch("fabric_test_drive.cli.login")
    def test_plan_needs_no_authentication_or_state(self, login):
        self.assertEqual(cli.main(["plan"]), 0)
        login.assert_not_called()
        self.assertFalse(self.state_path.exists())


if __name__ == "__main__":
    unittest.main()
