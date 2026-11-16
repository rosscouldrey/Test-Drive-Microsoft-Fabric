# Microsoft Fabric Test Drive

[![Open in GitHub Codespaces](https://github.com/codespaces/badge.svg)](https://codespaces.new/rosscouldrey/Test-Drive-Microsoft-Fabric?quickstart=1)

**Status: experimental deployment spike, not a finished one-click solution.**
The first scenario uses Northwind through Fabric's actual OData connector.
NYTaxi is a future scenario.

The goal is a browser-launched, populated Fabric workspace with a sales model
and report. The original data deployment path has passed in a live Fabric tenant.
The schema-enabled bronze/gold and semantic model deployment is awaiting live
tenant verification. Report authoring is planned separately.

## What this increment creates

| Object | Purpose |
| --- | --- |
| A new workspace on your existing capacity | Isolated demo environment |
| `Northwind` lakehouse | Schema-enabled lakehouse with eight `bronze` tables and five `gold` tables |
| Anonymous cloud OData connection | Public Northwind service; no source credentials |
| `Initialize Schemas` notebook | Creates the bronze and gold schemas before ingestion |
| `Ingest Northwind` pipeline | Sequential Copy activities using the OData connector into bronze |
| `Verify Northwind` PySpark notebook | Checks populated tables, order-detail joins, persisted sales rows, and a sales aggregation |
| `Northwind Model` semantic model | Exported Direct Lake model rebound to the new lakehouse |

Source entities: Customers, Orders, Order_Details, Products, Categories,
Employees, Suppliers, and Shippers.
OData `$select` projections exclude the binary `Categories.Picture` and
`Employees.Photo` fields, which are unsupported by this Copy path. Other scalar
fields are retained; source images are not part of this demo.

**Not implemented:** Power BI report, hosted
web wizard, capacity creation, and unattended GitHub Actions deployment.
The deployment reports data-stage success only after the pipeline and
assertion-bearing notebook jobs complete.

## Prerequisites and costs

- A GitHub account with Codespaces access and available allowance or billing.
- A Microsoft account in an Entra tenant with Fabric enabled, the required
  Fabric/Power BI entitlement, permission to create workspaces, and permission
  to assign one to an existing supported, active Fabric capacity.
- Tenant policies that permit Azure CLI delegated sign-in and the required
  Fabric APIs. Device-code authentication may be blocked by Conditional Access.
  Administrator consent may be required; this launcher does not bypass policy.
- Power BI API access to update and refresh the semantic model, Build/read access
  for its DAX check, and the tenant's **Dataset Execute Queries REST API** setting
  enabled. The deployment account must own the model and be able to read its source tables.
- Outbound access to Fabric, Power BI APIs, Microsoft sign-in, and `services.odata.org`.

No Azure subscription is required by the launcher when using an already
available Fabric capacity, but the capacity must support the deployed workloads.
A trial capacity is an option only if your tenant and account are eligible.
Fabric workloads and Codespaces can incur charges. Deleting the workspace
does **not** stop capacity billing.

## Launch

1. Select **Open in GitHub Codespaces** above and create the Codespace.
2. Wait for the dev container to finish installing Python dependencies and Azure CLI.
3. In the terminal, run:

   ```bash
   .venv/bin/fabric-test-drive plan
   .venv/bin/fabric-test-drive deploy
   ```

4. Enter your **tenant UUID**, complete the Microsoft device-code sign-in, select
   an accessible active capacity, enter a new workspace name, and confirm.

Only enter the displayed device code at Microsoft's sign-in page for a session
you initiated. The launcher never asks you to paste an access token or password.
Preflight checks happen before the first Fabric write. It checks API access,
the advertised OData connection creation contract, and a sample Northwind read;
it cannot prove workspace creation rights or actual connector execution.

The connection request uses the encryption setting advertised by the connector:
`Encrypted` when available, otherwise `NotEncrypted` if supported. The OData
connector can advertise only `NotEncrypted`; that API setting does not change
the configured source URL. Northwind remains anonymous and uses a fixed HTTPS
URL, and the launcher refuses an HTTP source.

To run the read-only checks separately:

```bash
.venv/bin/fabric-test-drive preflight --tenant <tenant-uuid>
```

For an existing, correctly authenticated Azure CLI session, add `--skip-login`.
Optional deployment arguments are `--tenant`, `--capacity`, and `--name`;
confirmation is still required for a new deployment.

### Expected successful result

The workspace has eight nonempty source tables in `bronze` and a star schema in
`gold`. The
notebook verifies unique/non-null gold keys, persisted row counts, every sale's
dimension matches, and a positive revenue aggregation.
The terminal prints the workspace link.
Notebook execution errors fail the deployment rather than showing success.
The model must also finish refreshing and return positive `Total Sales` and
`Order Count` values through a DAX query before deployment reports success.

### Explore the model in Fabric

Use the `gold` schema's tables in your model. A rerun of a schema-enabled
deployment consumes capacity and overwrites demo tables.

Use these many-to-one, single-direction relationships (dimensions filter sales):

| Fact column | Dimension key |
| --- | --- |
| `gold.fact_sales.customer_id` | `gold.dim_customer.customer_id` |
| `gold.fact_sales.product_id` | `gold.dim_product.product_id` |
| `gold.fact_sales.employee_id` | `gold.dim_employee.employee_id` |
| `gold.fact_sales.order_date` | `gold.dim_date.date` |

`fact_sales` has one row per order/product pair. Product includes category and
supplier names; customer includes company/city/country; employee includes name
and title. The date dimension covers every day from the first to last order.
Mark it as a date table, sort `month` by `month_number`, and use `year_month` for
a chronological sales trend.

The exported model includes sales, order, quantity, and time-intelligence measures.

### Export your manually authored semantic model

From the repository directory, export the model's definition as readable TMDL
files using your existing Azure CLI sign-in:

```bash
.venv/bin/python -m fabric_test_drive.export_model --model <semantic-model-uuid>
```

Find the semantic model UUID in its Fabric URL (the `dataset` ID, not the report
ID), or list the workspace's semantic models using Fabric's
`GET /v1/workspaces/{workspaceId}/semanticModels` API.
Tenant and workspace IDs default to `.fabric-test-drive/state.json`. To export
from another workspace, or without local deployment state, specify both:

```bash
.venv/bin/python -m fabric_test_drive.export_model \
  --tenant <tenant-uuid> --workspace <workspace-uuid> --model <semantic-model-uuid>
```

The script calls
`POST /v1/workspaces/{workspaceId}/semanticModels/{semanticModelId}/getDefinition?format=TMDL`,
waits for asynchronous exports, and decodes each definition part into
`templates/Northwind.SemanticModel/`. It does not change the model, deploy
resources, or modify deployment state. If authentication has expired, run
`az login --tenant <tenant-uuid> --use-device-code --allow-no-subscriptions`
and retry. The signed-in account needs permission to read the model definition.

Review the exported files for sensitive values and environment-specific data
source references before committing. Definitions are exported, not table data.
For example:

```bash
git switch -c add-northwind-model
git add templates/Northwind.SemanticModel
git diff --cached
git commit -m "Add authored Northwind semantic model"
git push -u origin add-northwind-model
```

Existing export directories are never overwritten. For subsequent exports, use
`--output templates/Northwind.SemanticModel-new`, review it, then replace the old
directory with the new one (including removing stale definition files).
The model deployment below uses this exported template. Report
export and deployment are not implemented.

### Test the exported model in a new deployment

Deployment creates `Northwind Model` after the gold tables pass
their checks, replacing the template's OneLake workspace and lakehouse IDs with
the new deployment's IDs. This is experimental and has not been validated in a
fresh end-to-end live deployment. It also sets the model's target storage mode to
`PremiumFiles` through the Power BI API. Live troubleshooting showed that the
target storage setting alone does not establish readiness: an initial refresh
could fail while a subsequent refresh and DAX query succeeded.

The launcher now waits for the target storage setting, starts a transactional
full refresh with up to two service-side retries (five minutes per attempt),
and polls the recorded refresh until completion. It then executes a DAX query
and requires positive `Total Sales` and `Order Count` values. It never treats a
failed refresh or HTTP 200 response containing query errors as success.
This automatic readiness flow still needs fresh live-tenant verification.

Use a separate clone or Codespace so the original workspace and its state remain
untouched. In that new directory, install the package as under Local development
and run:

```bash
.venv/bin/fabric-test-drive deploy --skip-login
```

Select a capacity, enter a **new workspace name**, and type `deploy` to confirm.
A recorded model is reused, not updated from subsequent template edits. Manual
refresh should not be necessary. After deployment succeeds, you can independently
run the same DAX check in Fabric:

```DAX
EVALUATE ROW("Sales", [Total Sales], "Orders", [Order Count])
```

Additional manual checks should test dimensions filtering sales and the date
measures; the automated query does not validate every measure or relationship.
Keep the new directory's state for resuming or guarded cleanup.

## Resume and cleanup

**Existing non-schema deployments cannot be converted in place.** This version
refuses to deploy over their saved state and never automatically deletes them.
Keep the old directory and state if you want to retain the workspace. To create
a separate schema-enabled workspace, use a fresh clone/directory, install the
package as shown under Local development, and deploy with a new workspace name.
Alternatively, if you no longer need anything in the old workspace, run the
guarded `cleanup` command from its original directory before deploying again.
Cleanup deletes the entire recorded workspace and connection.

Run the same `deploy` command from the same directory to resume. Resource IDs
and job URLs are saved in the gitignored `.fabric-test-drive/state.json`.
Already-recorded items are reused, pending jobs are polled, and failed jobs can
be retried on the next invocation. A fully successful deployment is rerunnable:
the next run overwrites the bronze and gold tables.
Before starting a new run of an existing ingestion pipeline, the launcher updates
its definition from the checked-out code. Active runs are polled without changing
their definition, and completed stages in an interrupted deployment are reused.
The verification notebook definition is also updated before a new verification
run; the schema-initialization notebook is reused.
Model refresh IDs and completed refresh/query stages are saved separately.
An interrupted deployment polls the same refresh rather than submitting another.
If the refresh fails after its service retries, inspect its error in refresh
history before rerunning `deploy`; the next invocation can retry a known failed
refresh without repeating completed data stages. A timeout preserves the refresh
ID so it can be polled on resume. The refresh wait is bounded to 30 minutes per
invocation. A completed refresh is not repeated just because query verification
fails (for example, because Execute Queries is disabled).

**Keep this state file until cleanup is complete.** Closing a Codespace does
not delete Fabric objects; deleting the Codespace can lose cleanup state.
The state file contains identifiers, not access tokens. Do not commit it.
If a create request succeeds on the server but its response is lost, the script
does not blindly retry it or adopt an object by name. Inspect Fabric and remove
any unrecorded resource manually before retrying. An unresolved create leaves a
`pending_create` entry in the state file and blocks further writes and cleanup.
After verifying that the attempted resource is absent (and removing it if
necessary), remove only that entry from the state file to allow another attempt.
Never clear it while an asynchronous creation may still be running.
An ambiguous job submission similarly leaves `pending_job`. Check the item's run
history, wait for or cancel any run, then remove that entry before retrying.
Do not delete the state file to work around an unresolved operation.
Long-running Fabric operations are polled by their `x-ms-operation-id` on
`api.fabric.microsoft.com`; the access token is never sent to other hosts, even
when Fabric returns a regional `Location` header. Power BI refreshes are polled
on `api.powerbi.com` by their `x-ms-request-id` when available, without sending
the token to a regional `Location` host.

```bash
.venv/bin/fabric-test-drive cleanup
az logout
```

Cleanup checks the workspace's deployment marker and recorded connection name,
requires typing the workspace name, and refuses while a recorded job is active.
This includes a recorded semantic model refresh.
It deletes the **entire recorded workspace**, including anything you subsequently
added, and the connection created by this deployment. Do not share that connection
with other workspaces. It never deletes or pauses a capacity. If Fabric reports
that the recorded workspace was not found, cleanup records it as already deleted
and continues with the connection. Other workspace errors, a missing connection,
or failed ownership checks stop cleanup for inspection.

Azure CLI maintains a local authentication cache. Use a private Codespace,
sign out when finished, and do not expose its terminal or authentication cache.
This project does not store a deployment client secret in GitHub.

## Local development

Python 3.11+ is required. Azure CLI is needed only for tenant operations.

```bash
python -m venv .venv
.venv/bin/python -m pip install -e .
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/fabric-test-drive plan
```

Tests use simulated API responses and do not create or delete cloud resources.

## Delivery gates

1. **Data-path spike (current):** validate sign-in, OData connection creation,
   Copy ingestion, notebook attachment, execution, and assertions in a live tenant.
2. **Golden demo:** add conformed dimensions, a semantic model with sales measures,
   and a report. Prove deployment and binding without manual edits.
3. **Repeatability:** validate interrupted runs, cleanup, tenant-policy errors,
   endpoint readiness, throttling, and deployment from a second clean environment.
4. **Launch polish:** add screenshots, a walkthrough, and measured deployment time.
   Introduce NYTaxi only after Northwind passes.

The service is a shared public sample endpoint, not a production availability
commitment. Source unavailability is a visible failure, never an undisclosed
switch to bundled data or notebook HTTP ingestion.

## References

- [Fabric workspace creation](https://learn.microsoft.com/en-us/rest/api/fabric/core/workspaces/create-workspace)
- [Discover supported connection types](https://learn.microsoft.com/en-us/rest/api/fabric/core/connections/list-supported-connection-types)
- [Create a connection](https://learn.microsoft.com/en-us/rest/api/fabric/core/connections/create-connection)
- [Fabric OData connector](https://learn.microsoft.com/en-us/fabric/data-factory/connector-odata)
- [OData Copy activity](https://learn.microsoft.com/en-us/fabric/data-factory/connector-odata-copy-activity)
- [Northwind OData service](https://services.odata.org/V4/Northwind/Northwind.svc/)
