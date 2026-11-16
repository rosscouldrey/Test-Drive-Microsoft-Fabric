# Microsoft Fabric Test Drive

Deploy a ready-to-explore Northwind sales environment into your Microsoft Fabric
tenant: sample data, bronze and gold lakehouse tables, a Direct Lake semantic
model, and a Power BI report.

[![Open in GitHub Codespaces](https://github.com/codespaces/badge.svg)](https://codespaces.new/rosscouldrey/Test-Drive-Microsoft-Fabric?quickstart=1)

**A guided demo, not a production deployment framework.** The complete deployment
has been exercised in a live Fabric tenant, including report rendering. Results
depend on your tenant policies, permissions, capacity, and the public sample
service. Fabric capacity and GitHub Codespaces usage can incur charges.

## Before you start

You need:

- A GitHub account with Codespaces access and available allowance or billing.
- A Microsoft Entra tenant with Fabric enabled, the required Fabric/Power BI
  entitlement, and permission to create workspaces and assign them to an
  **existing, active capacity that supports Fabric lakehouses, Spark, and Power BI**.
  Power BI Premium Per User alone is not sufficient. A Fabric trial is an option
  only if your tenant and account are eligible.
- Your **tenant ID** (a UUID), available in the Microsoft Entra admin center
  under Identity > Overview. Ask your administrator if you cannot find it.
- Tenant policies allowing Azure CLI delegated device-code sign-in, Fabric and
  Power BI APIs, and the **Dataset Execute Queries REST API** tenant setting.
  Administrator consent may be required.
- Permission for the deployment account to own, update, refresh, and query the
  semantic model and read its lakehouse tables. The launcher creates the workspace
  with that account; do not remove its access while deployment runs.
- Network access to Microsoft sign-in, Fabric, Power BI APIs, and
  `https://services.odata.org`.

The launcher uses your existing capacity; it does not provision one. No Azure
subscription is required by the launcher when using an already available capacity.
**Deleting the demo workspace does not stop capacity billing.**

## Quick start: run in your browser

1. Select **Open in GitHub Codespaces** above and create a Codespace from `main`.
   Wait for setup to finish; it installs Python dependencies and Azure CLI.
2. In the Codespace terminal, preview the deployment and then start it:

   ```bash
   .venv/bin/fabric-test-drive plan
   .venv/bin/fabric-test-drive deploy
   ```

3. Enter your tenant UUID and complete the Microsoft device-code sign-in.
   Only enter the displayed code at Microsoft's sign-in page for a session you
   initiated. The launcher never asks you to paste a password or access token.
4. Choose the number of a supported active capacity, enter a **new workspace
   name**, and type the exact word **`deploy`** at the confirmation prompt:

   ```text
   New workspace name: Northwind Test Drive
   Type deploy to continue: deploy
   ```

5. Leave the terminal running. Schema setup, ingestion, Spark verification, model
   initialization, and refresh can take several minutes or longer depending on
   capacity availability. **You should not need to refresh the model manually.**
6. When deployment completes, open the printed **Report** link. Explore each
   page, try the filters, and check that visuals display data.

Preflight checks API access, connection metadata, and the public Northwind service
before the first write. It cannot guarantee workspace creation permission,
connector execution, or all tenant-policy requirements.

### What gets created

```text
Public Northwind OData service
  -> Fabric Copy pipeline
  -> Northwind lakehouse: bronze source tables -> gold star schema
  -> Northwind Model: Direct Lake relationships and measures
  -> Northwind Report: sales analysis
```

| Resource | Purpose |
| --- | --- |
| New workspace | Isolated demo on your selected capacity |
| `Northwind` lakehouse | Eight bronze source tables and five gold tables |
| Anonymous OData connection | Reads the public HTTPS Northwind source |
| `Initialize Schemas` notebook | Creates the bronze and gold schemas |
| `Ingest Northwind` pipeline | Copies source data using Fabric's OData connector |
| `Verify Northwind` notebook | Builds gold tables and checks rows, keys, joins, and sales |
| `Northwind Model` | Direct Lake model bound to this deployment's lakehouse |
| `Northwind Report` | Authored report bound to this deployment's model |

The gold tables are `fact_sales`, `dim_customer`, `dim_product`, `dim_employee`,
and `dim_date`. The model includes sales, order, quantity, and time-intelligence
measures. Northwind is public sample data, not your organization's data.
Source images and binary fields are excluded.

**Deployment succeeds only after** the notebooks complete, a model refresh
finishes, `Total Sales` and `Order Count` return positive DAX results, and the
report's model binding is verified. Visual rendering and every measure or report
interaction are not automatically tested; check the report in Fabric.

## Sign-in, retries, and separate environments

To reuse a valid Azure CLI session:

```bash
.venv/bin/fabric-test-drive deploy --skip-login
```

If authentication expires, run without `--skip-login`. Tenant policies may still
require reauthentication.

If deployment stops, **preserve `.fabric-test-drive/state.json`** and rerun from
the **same directory**. The launcher reuses recorded resources and completed
stages and resumes recorded jobs. Inspect the error first; permission or
configuration failures require correction.

A rerun after a successful deployment overwrites the demo's bronze and gold
tables and refreshes the model. Recorded models and reports are reused, not
overwritten with later template edits.

Each checkout directory tracks **one deployment**. To create another environment,
use a separate clone or Codespace and a different workspace name. Do not delete
or edit state to switch environments.

**Keep the directory and its state until cleanup is complete.** Closing a
Codespace does not delete Fabric resources. Deleting the Codespace can lose the
state needed for guarded cleanup. State contains resource identifiers, not access
tokens; it is gitignored and must not be committed.

## Delete the demo

From the deployment's original directory:

```bash
.venv/bin/fabric-test-drive cleanup --skip-login
```

Check the workspace name, then type that exact name to confirm.
**This deletes the entire recorded workspace**, including models, reports, or
other content you added manually, and the deployment's OData connection.
Export anything you want to keep first. Do not share that connection with other
workspaces.

Cleanup checks ownership and refuses while a recorded job or model refresh is
active. If the workspace was manually deleted, an explicit `WorkspaceNotFound`
response is treated as already deleted and connection cleanup continues. Other
errors stop cleanup for inspection.

Cleanup never deletes or pauses the capacity. Manage capacity costs separately.
When finished with the Codespace, sign out:

```bash
az logout
```

## Run locally or provide IDs explicitly

Use Python 3.11+, Git, and Azure CLI on Linux, macOS, or WSL. Run from the
repository checkout so the launcher can access the included model/report templates.

```bash
git clone https://github.com/rosscouldrey/Test-Drive-Microsoft-Fabric.git
cd Test-Drive-Microsoft-Fabric
python -m venv .venv
.venv/bin/python -m pip install -e .
.venv/bin/fabric-test-drive deploy
```

You can provide IDs instead of entering them interactively; confirmation is still
required:

```bash
.venv/bin/fabric-test-drive deploy \
  --tenant <tenant-uuid> \
  --capacity <capacity-uuid> \
  --name "Northwind Test Drive"
```

For read-only checks, use `plan` (no sign-in or API calls) or:

```bash
.venv/bin/fabric-test-drive preflight --tenant <tenant-uuid>
```

`--skip-login` also works with `preflight` and `cleanup`.

## Troubleshooting and customization

See [Troubleshooting](docs/troubleshooting.md) for authentication, capacity,
Northwind availability, model refresh, and interrupted-operation recovery.
See [Development and customization](docs/development.md) to export your authored
model/report definitions, change templates, or run the tests.

This project does not create capacities, provide a hosted web wizard, or perform
unattended GitHub Actions deployment. It uses a public sample service with no
availability guarantee and does not silently substitute bundled data or another
ingestion mechanism.
