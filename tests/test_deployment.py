import base64
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch

import requests

from fabric_test_drive import cli
from fabric_test_drive.api import DeploymentError, Fabric
from fabric_test_drive.artifacts import ENTITIES, SOURCE, notebook, pipeline, schema_notebook


TENANT = "11111111-1111-1111-1111-111111111111"
CAPACITY = "22222222-2222-2222-2222-222222222222"
WORKSPACE = "33333333-3333-3333-3333-333333333333"
LAKEHOUSE = "44444444-4444-4444-4444-444444444444"
CONNECTION = "55555555-5555-5555-5555-555555555555"
PIPELINE = "66666666-6666-6666-6666-666666666666"
NOTEBOOK = "77777777-7777-7777-7777-777777777777"
SETUP = "88888888-8888-8888-8888-888888888888"
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

    @patch("fabric_test_drive.api.time.sleep")
    def test_definition_update_polls_until_succeeded_without_requesting_a_result(self, sleep):
        self.api.session.request.side_effect = [
            response(status=202, headers={"Location": "operations/update"}),
            response({"status": "Running"}),
            response({"status": "Succeeded"}),
        ]
        self.api.update_definition(WORKSPACE, PIPELINE, {"parts": []})
        self.assertEqual(self.api.session.request.call_count, 3)
        first = self.api.session.request.call_args_list[0]
        self.assertTrue(first.args[1].endswith(f"/items/{PIPELINE}/updateDefinition"))
        self.assertEqual(first.kwargs["json"], {"definition": {"parts": []}})

    @patch("fabric_test_drive.api.time.sleep")
    def test_failed_definition_update_is_reported(self, sleep):
        self.api.session.request.side_effect = [
            response(status=202, headers={"Location": "operations/update"}),
            response({"status": "Failed"}),
        ]
        with self.assertRaisesRegex(DeploymentError, "Definition update Failed"):
            self.api.update_definition(WORKSPACE, PIPELINE, {"parts": []})

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
    def test_create_polls_fabric_api_by_operation_id_despite_regional_location(self, sleep):
        operation = "88888888-8888-8888-8888-888888888888"
        self.api.session.request.side_effect = [
            response(status=202, headers={
                "Location": f"https://wabi-west-us-redirect.analysis.windows.net/v1/operations/{operation}",
                "x-ms-operation-id": operation,
            }),
            response({"status": "Running"}),
            response({"status": "Succeeded"}),
            response({"id": NOTEBOOK}),
        ]
        self.assertEqual(self.api.create("items", {}), {"id": NOTEBOOK})
        polled = [call.args[1] for call in self.api.session.request.call_args_list[1:]]
        base = "https://api.fabric.microsoft.com/v1/operations/"
        self.assertEqual(polled, [
            base + operation, base + operation, base + operation + "/result",
        ])

    def test_untrusted_location_without_operation_id_is_not_followed(self):
        self.api.session.request.return_value = response(status=202, headers={
            "Location": "https://example.com/v1/operations/operation",
        })
        with self.assertRaisesRegex(DeploymentError, "untrusted polling URL"):
            self.api.create("items", {})
        self.api.session.request.assert_called_once()

    def test_invalid_operation_id_is_not_used_to_build_a_url(self):
        self.api.session.request.return_value = response(status=202, headers={
            "Location": "operations/operation", "x-ms-operation-id": "../workspaces",
        })
        with self.assertRaisesRegex(DeploymentError, "invalid operation ID"):
            self.api.create("items", {})
        self.api.session.request.assert_called_once()

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
    def test_binary_fields_are_excluded_by_odata_projection(self):
        content = pipeline(WORKSPACE, LAKEHOUSE, CONNECTION)
        decoded = json.loads(base64.b64decode(content["parts"][0]["payload"]))
        sources = {
            activity["name"]: activity["typeProperties"]["source"]
            for activity in decoded["properties"]["activities"]
        }
        category_fields = sources["Copy_categories"]["query"].removeprefix("$select=").split(",")
        self.assertEqual(category_fields, ["CategoryID", "CategoryName", "Description"])
        employee_fields = sources["Copy_employees"]["query"].removeprefix("$select=").split(",")
        self.assertNotIn("Photo", employee_fields)
        self.assertIn("EmployeeID", employee_fields)
        self.assertIn("ReportsTo", employee_fields)
        self.assertIn("PhotoPath", employee_fields)
        self.assertNotIn("query", sources["Copy_order_details"])

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
            self.assertEqual(dataset["typeProperties"]["schema"], "bronze")
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

    def test_notebooks_initialize_schemas_and_use_qualified_tables(self):
        setup = json.loads(base64.b64decode(schema_notebook(WORKSPACE, LAKEHOUSE)["parts"][0]["payload"]))
        code = "".join(setup["cells"][0]["source"])
        self.assertIn("CREATE SCHEMA IF NOT EXISTS bronze", code)
        self.assertIn("CREATE SCHEMA IF NOT EXISTS gold", code)
        self.assertEqual(setup["metadata"]["dependencies"]["lakehouse"]["default_lakehouse"], LAKEHOUSE)
        compile(code, "<schema-notebook>", "exec")
        verify = json.loads(base64.b64decode(notebook(WORKSPACE, LAKEHOUSE)["parts"][0]["payload"]))
        code = "".join(verify["cells"][0]["source"])
        self.assertIn('spark.table(f"bronze.{name}")', code)
        self.assertIn('name = f"gold.{name}"', code)
        self.assertIn('spark.table("gold.fact_sales")', code)

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

    def test_connection_accepts_advertised_encryption_without_changing_https_source(self):
        for advertised, expected in (
            (["NotEncrypted"], "NotEncrypted"),
            (["Encrypted"], "Encrypted"),
            (["NotEncrypted", "Encrypted"], "Encrypted"),
        ):
            with self.subTest(advertised=advertised):
                types = json.loads(json.dumps(CONNECTION_TYPES))
                types[0]["supportedConnectionEncryptionTypes"] = advertised
                result = cli.connection_body(types, "Northwind-test")
                self.assertEqual(
                    result["credentialDetails"]["connectionEncryption"], expected
                )
                self.assertEqual(
                    result["connectionDetails"]["parameters"][0]["value"],
                    "https://services.odata.org/V4/Northwind/Northwind.svc/",
                )
                self.assertEqual(
                    result["credentialDetails"]["credentials"],
                    {"credentialType": "Anonymous"},
                )

    def test_connection_rejects_unknown_encryption_or_nonanonymous_authentication(self):
        for field, value in (
            ("supportedConnectionEncryptionTypes", []),
            ("supportedConnectionEncryptionTypes", ["Unknown"]),
            ("supportedCredentialTypes", ["Basic"]),
        ):
            with self.subTest(field=field, value=value):
                types = json.loads(json.dumps(CONNECTION_TYPES))
                types[0][field] = value
                with self.assertRaisesRegex(DeploymentError, "feasibility blocker"):
                    cli.connection_body(types, "Northwind-test")

    def test_connection_refuses_http_even_when_notencrypted_is_advertised(self):
        types = json.loads(json.dumps(CONNECTION_TYPES))
        types[0]["supportedConnectionEncryptionTypes"] = ["NotEncrypted"]
        with patch.object(cli, "SOURCE", SOURCE.replace("https:", "http:")):
            with self.assertRaisesRegex(DeploymentError, "must use HTTPS"):
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
            "verified": False, "lakehouse_layout": "bronze-gold-schemas",
        }

    def test_failed_ingestion_updates_definition_before_retry_without_recreating(self):
        state = self.state()
        state["jobs"]["ingestion"] = "https://api.fabric.microsoft.com/v1/jobs/failed"
        api = Mock()
        api.request.return_value = response({"status": "Failed"})
        definition = pipeline(WORKSPACE, LAKEHOUSE, CONNECTION)
        api.run.side_effect = lambda ws, item, kind, started: started(
            "https://api.fabric.microsoft.com/v1/jobs/retry"
        )
        cli.run_stage(api, state, "ingestion", PIPELINE, "Pipeline", definition)
        api.update_definition.assert_called_once_with(WORKSPACE, PIPELINE, definition)
        api.create.assert_not_called()
        methods = [call[0] for call in api.mock_calls]
        self.assertLess(methods.index("update_definition"), methods.index("run"))
        self.assertEqual(state["completed"], ["ingestion"])

    def test_active_or_completed_ingestion_does_not_update_definition_or_start_again(self):
        for completed in (False, True):
            with self.subTest(completed=completed):
                state = self.state()
                state["jobs"]["ingestion"] = "https://api.fabric.microsoft.com/v1/jobs/current"
                if completed:
                    state["completed"].append("ingestion")
                api = Mock()
                api.request.return_value = response({"status": "InProgress"})
                cli.run_stage(
                    api, state, "ingestion", PIPELINE, "Pipeline",
                    pipeline(WORKSPACE, LAKEHOUSE, CONNECTION),
                )
                api.update_definition.assert_not_called()
                api.run.assert_not_called()
                self.assertEqual(api.wait_job.call_count, 0 if completed else 1)

    def test_definition_update_failure_prevents_job_submission(self):
        api = Mock()
        api.update_definition.side_effect = DeploymentError("update failed")
        state = self.state()
        with self.assertRaisesRegex(DeploymentError, "update failed"):
            cli.run_stage(api, state, "ingestion", PIPELINE, "Pipeline", {"parts": []})
        api.run.assert_not_called()
        self.assertNotIn("pending_job", state)

    def test_resume_reuses_objects_and_completed_ingestion_after_notebook_failure(self):
        api = Mock()
        api.list.return_value = []
        api.create.side_effect = [
            {"id": WORKSPACE}, {"id": LAKEHOUSE}, {"id": CONNECTION},
            {"id": PIPELINE}, {"id": NOTEBOOK}, {"id": SETUP}, {"id": "model-id"},
        ]
        items = {
            LAKEHOUSE: ("Lakehouse", "Northwind"),
            PIPELINE: ("DataPipeline", "Ingest Northwind"),
            NOTEBOOK: ("Notebook", "Verify Northwind"),
            SETUP: ("Notebook", "Initialize Schemas"),
            "model-id": ("SemanticModel", "Northwind Model"),
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
            if item == NOTEBOOK and attempt["fail"]:
                raise DeploymentError("notebook failed")

        api.run.side_effect = run
        with self.assertRaisesRegex(DeploymentError, "notebook failed"):
            cli.deploy(api, None, TENANT, CAPACITY, "Northwind Test", CONNECTION_TYPES)
        state = cli.load()
        self.assertNotIn("semantic_model_id", state)
        self.assertEqual(api.create.call_count, 6)
        self.assertEqual(state["completed"], ["schema_setup", "ingestion"])
        self.assertFalse(state["verified"])
        self.assertIn("verification", state["jobs"])
        self.assertEqual(
            api.create.call_args_list[1].args[1]["creationPayload"],
            {"enableSchemas": True},
        )
        self.assertEqual([call.args[1] for call in api.run.call_args_list], [SETUP, PIPELINE, NOTEBOOK])
        attempt["fail"] = False
        cli.deploy(api, state, TENANT, CAPACITY, "Northwind Test", CONNECTION_TYPES)
        self.assertEqual(api.create.call_count, 7)
        self.assertEqual(api.run.call_count, 4)
        api.update_definition.assert_called_once_with(
            WORKSPACE, NOTEBOOK, notebook(WORKSPACE, LAKEHOUSE),
        )
        self.assertTrue(cli.load()["verified"])
        self.assertEqual(api.run.call_args.args[2], "RunNotebook")
        cli.deploy(api, cli.load(), TENANT, CAPACITY, "Northwind Test", CONNECTION_TYPES)
        self.assertEqual(api.create.call_count, 7)
        self.assertEqual(api.run.call_count, 7)
        self.assertEqual(
            api.update_definition.call_args.args,
            (WORKSPACE, NOTEBOOK, notebook(WORKSPACE, LAKEHOUSE)),
        )

    def test_legacy_state_is_rejected_without_writes_or_state_changes(self):
        state = self.state()
        del state["lakehouse_layout"]
        state["verified"] = True
        cli.save(state)
        before = self.state_path.read_text()
        api = Mock()
        with self.assertRaisesRegex(DeploymentError, "cannot be upgraded in place"):
            cli.deploy(api, state, TENANT, CAPACITY, "Northwind Test", CONNECTION_TYPES)
        api.create.assert_not_called()
        api.run.assert_not_called()
        api.update_definition.assert_not_called()
        self.assertEqual(self.state_path.read_text(), before)

    @patch("fabric_test_drive.cli.run_stage")
    def test_model_is_created_after_data_verification_and_reused(self, run_stage):
        state = self.state()
        state.update(lakehouse_id=LAKEHOUSE, connection_id=CONNECTION,
                     pipeline_id=PIPELINE, notebook_id=NOTEBOOK, schema_notebook_id=SETUP)
        cli.save(state)
        api = Mock()
        items = {
            LAKEHOUSE: ("Lakehouse", "Northwind"),
            PIPELINE: ("DataPipeline", "Ingest Northwind"),
            NOTEBOOK: ("Notebook", "Verify Northwind"),
            SETUP: ("Notebook", "Initialize Schemas"),
            "model-id": ("SemanticModel", "Northwind Model"),
        }

        def request(method, path):
            if path == f"workspaces/{WORKSPACE}":
                return response({"description": state["marker"], "capacityId": CAPACITY})
            if path == f"connections/{CONNECTION}":
                return response({"displayName": state["connection_name"]})
            item_id = path.rsplit("/", 1)[-1]
            kind, name = items[item_id]
            return response({"id": item_id, "type": kind, "displayName": name})

        api.request.side_effect = request
        def create(path, body):
            self.assertEqual(run_stage.call_count, 3)
            self.assertEqual(run_stage.call_args.args[2], "verification")
            return {"id": "model-id"}

        api.create.side_effect = create
        cli.deploy(api, state, TENANT, CAPACITY, "Northwind Test", CONNECTION_TYPES)
        body = api.create.call_args.args[1]
        self.assertEqual(body["type"], "SemanticModel")
        self.assertEqual(body["definition"]["format"], "TMDL")
        self.assertEqual([call.args[2] for call in run_stage.call_args_list],
                         ["schema_setup", "ingestion", "verification"])
        self.assertEqual(cli.load()["semantic_model_id"], "model-id")
        cli.deploy(api, cli.load(), TENANT, CAPACITY, "Northwind Test", CONNECTION_TYPES)
        api.create.assert_called_once()

    @patch("fabric_test_drive.cli.run_stage")
    def test_failed_model_creation_preserves_data_stages_and_blocks_blind_retry(self, run_stage):
        state = self.state()
        state.update(lakehouse_id=LAKEHOUSE, connection_id=CONNECTION,
                     pipeline_id=PIPELINE, notebook_id=NOTEBOOK, schema_notebook_id=SETUP)
        state["completed"] = ["schema_setup", "ingestion", "verification"]
        cli.save(state)
        api = Mock()
        items = {
            LAKEHOUSE: ("Lakehouse", "Northwind"),
            PIPELINE: ("DataPipeline", "Ingest Northwind"),
            NOTEBOOK: ("Notebook", "Verify Northwind"),
            SETUP: ("Notebook", "Initialize Schemas"),
        }

        def request(method, path):
            if path == f"workspaces/{WORKSPACE}":
                return response({"description": state["marker"], "capacityId": CAPACITY})
            if path == f"connections/{CONNECTION}":
                return response({"displayName": state["connection_name"]})
            item_id = path.rsplit("/", 1)[-1]
            kind, name = items[item_id]
            return response({"id": item_id, "type": kind, "displayName": name})

        api.request.side_effect = request
        api.create.side_effect = DeploymentError("response lost")
        with self.assertRaisesRegex(DeploymentError, "response lost"):
            cli.deploy(api, state, TENANT, CAPACITY, "Northwind Test", CONNECTION_TYPES)
        saved = cli.load()
        self.assertEqual(saved["pending_create"], "semantic_model_id")
        self.assertEqual(saved["completed"], ["schema_setup", "ingestion", "verification"])
        self.assertFalse(saved["verified"])
        with self.assertRaisesRegex(DeploymentError, "unresolved outcome"):
            cli.deploy(api, saved, TENANT, CAPACITY, "Northwind Test", CONNECTION_TYPES)
        api.create.assert_called_once()

    def test_schema_setup_failure_prevents_ingestion(self):
        api = Mock()
        api.list.return_value = []
        api.create.side_effect = [
            {"id": WORKSPACE}, {"id": LAKEHOUSE}, {"id": CONNECTION},
            {"id": PIPELINE}, {"id": NOTEBOOK}, {"id": SETUP},
        ]
        api.request.side_effect = lambda method, path: response({
            "description": cli.load()["marker"], "capacityId": CAPACITY,
        })
        def fail(workspace, item, kind, started):
            started(f"https://api.fabric.microsoft.com/v1/jobs/{item}")
            raise DeploymentError("schema creation failed")
        api.run.side_effect = fail
        with self.assertRaisesRegex(DeploymentError, "schema creation failed"):
            cli.deploy(api, None, TENANT, CAPACITY, "Northwind Test", CONNECTION_TYPES)
        self.assertEqual([call.args[1] for call in api.run.call_args_list], [SETUP])
        self.assertNotIn("ingestion", cli.load()["completed"])

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

    def test_cleanup_continues_when_workspace_was_deleted_manually(self):
        state = self.state()
        state["connection_id"] = CONNECTION
        state["jobs"]["ingestion"] = "https://api.fabric.microsoft.com/v1/jobs/job"
        cli.save(state)
        api = Fabric(TENANT)
        api.token = Mock(return_value="test-token")
        api.session.request = Mock(side_effect=[
            response({"errorCode": "WorkspaceNotFound"}, status=404),
            response({"displayName": state["connection_name"]}),
            response(status=200),
        ])
        with patch("builtins.input", return_value=state["workspace_name"]):
            cli.cleanup(api, state)
        calls = api.session.request.call_args_list
        self.assertEqual(
            [(call.args[0], call.args[1].split("/v1/")[1]) for call in calls],
            [("GET", f"workspaces/{WORKSPACE}"),
             ("GET", f"connections/{CONNECTION}"),
             ("DELETE", f"connections/{CONNECTION}")],
        )
        self.assertFalse(self.state_path.exists())

    def test_missing_workspace_is_saved_before_connection_check_and_resumable(self):
        state = self.state()
        state["connection_id"] = CONNECTION
        cli.save(state)
        api = Fabric(TENANT)
        api.token = Mock(return_value="test-token")
        api.session.request = Mock(side_effect=[
            response({"errorCode": "WorkspaceNotFound"}, status=404),
            response({"errorCode": "Forbidden"}, status=403),
        ])
        with self.assertRaisesRegex(DeploymentError, "HTTP 403"):
            cli.cleanup(api, state)
        self.assertTrue(cli.load()["workspace_deleted"])
        api.session.request.reset_mock()
        api.session.request.side_effect = [
            response({"displayName": state["connection_name"]}),
            response(status=200),
        ]
        with patch("builtins.input", return_value=state["workspace_name"]):
            cli.cleanup(api, cli.load())
        self.assertEqual(api.session.request.call_count, 2)
        self.assertFalse(self.state_path.exists())

    def test_other_workspace_errors_do_not_mark_it_deleted(self):
        for status, data in [
            (403, {"errorCode": "Forbidden"}),
            (404, {"errorCode": "NotFound"}),
            (500, {"errorCode": "WorkspaceNotFound"}),
            (404, None),
        ]:
            with self.subTest(status=status, data=data):
                state = self.state()
                cli.save(state)
                api = Fabric(TENANT)
                api.token = Mock(return_value="test-token")
                api.session.request = Mock(return_value=response(data, status=status))
                with self.assertRaises(DeploymentError):
                    cli.cleanup(api, state)
                self.assertNotIn("workspace_deleted", cli.load())
                api.session.request.assert_called_once()

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
