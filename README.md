# Microsoft Fabric Test Drive

[![Open in GitHub Codespaces](https://github.com/codespaces/badge.svg)](https://codespaces.new/rosscouldrey/Test-Drive-Microsoft-Fabric?quickstart=1)

**Status: experimental deployment spike, not a finished one-click solution.**
The first scenario uses Northwind through Fabric's actual OData connector.
NYTaxi is a future scenario.

The goal is a browser-launched, populated Fabric workspace with a sales model
and report. This first increment implements the data deployment path; it has
not yet been verified against a live Fabric tenant. API-contract fixtures are
not evidence of successful tenant deployment.

## What this increment creates

| Object | Purpose |
| --- | --- |
| A new workspace on your existing capacity | Isolated demo environment |
| `Northwind` lakehouse | Eight source tables and a `fact_sales` Delta table |
| Anonymous cloud OData connection | Public Northwind service; no source credentials |
| `Ingest Northwind` pipeline | Sequential Copy activities using the OData connector |
| `Verify Northwind` PySpark notebook | Checks populated tables, order-detail joins, persisted sales rows, and a sales aggregation |

Source entities: Customers, Orders, Order_Details, Products, Categories,
Employees, Suppliers, and Shippers.

**Not implemented:** dimension tables, semantic model, Power BI report, hosted
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
- Outbound access to Fabric, Microsoft sign-in, and `services.odata.org`.

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

The workspace has eight nonempty source tables and a populated `fact_sales`
table. The notebook verifies that every order-detail row joins to exactly one
order, checks required sales fields, reads the persisted fact table back, and
runs a positive revenue aggregation. The terminal prints the workspace link.
Notebook execution errors fail the deployment rather than showing success.

The pipeline JSON, anonymous OData connection, lakehouse table destination,
Spark attachment, and delegated job execution still require a real tenant
smoke test. SQL endpoint readiness and model/report binding are later gates.

## Resume and cleanup

Run the same `deploy` command from the same directory to resume. Resource IDs
and job URLs are saved in the gitignored `.fabric-test-drive/state.json`.
Already-recorded items are reused, pending jobs are polled, and failed jobs can
be retried on the next invocation. A fully successful deployment is rerunnable:
the next run overwrites the eight source tables and `fact_sales`.
Existing item definitions are reused, not upgraded. Use a new deployment
directory/workspace to test changed definitions.

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

```bash
.venv/bin/fabric-test-drive cleanup
az logout
```

Cleanup checks the workspace's deployment marker and recorded connection name,
requires typing the workspace name, and refuses while a recorded job is active.
It deletes the **entire recorded workspace**, including anything you subsequently
added, and the connection created by this deployment. Do not share that connection
with other workspaces. It never deletes or pauses a capacity. If an object has
already been removed manually or ownership checks fail, cleanup stops for inspection.

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
