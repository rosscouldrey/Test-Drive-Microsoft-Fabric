# Development and customization

For deploying the supplied demo, start with the [quick start](../README.md).
This guide covers source changes and bringing authored Fabric items into Git.

## Local development

Use Python 3.11+ from a repository checkout. Azure CLI is required only for live
tenant operations.

```bash
python -m venv .venv
.venv/bin/python -m pip install -e .
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/fabric-test-drive plan
```

Tests simulate API responses; they do not create or delete cloud resources.
Run live deployment checks in an isolated workspace with its own local state.
The package reads model/report templates from the checkout; a standalone wheel
installation is not the supported launch path.

| Path | Purpose |
| --- | --- |
| `src/fabric_test_drive/cli.py` | Deployment, state, resume, and guarded cleanup |
| `src/fabric_test_drive/api.py` | Fabric/Power BI authentication and API operations |
| `src/fabric_test_drive/artifacts.py` | Ingestion pipeline and Spark notebooks |
| `src/fabric_test_drive/model.py` | Lakehouse binding, model refresh, and DAX checks |
| `src/fabric_test_drive/report.py` | Report model binding and verification |
| `templates/` | Authored semantic model and report definitions |
| `tests/` | Simulated API and definition tests |

## Data and model

The connector reads Customers, Orders, Order_Details, Products, Categories,
Employees, Suppliers, and Shippers. Binary `Categories.Picture` and
`Employees.Photo` columns are excluded. Gold contains one sales row per
order/product pair and customer, product, employee, and date dimensions.

| Fact key | Dimension key |
| --- | --- |
| `fact_sales.customer_id` | `dim_customer.customer_id` |
| `fact_sales.product_id` | `dim_product.product_id` |
| `fact_sales.employee_id` | `dim_employee.employee_id` |
| `fact_sales.order_date` | `dim_date.date` |

The gold notebook checks keys, persisted counts, dimension matches, and positive
sales. Deployment verifies the model's Total Sales and Order Count measures,
not every relationship or time-intelligence calculation.

## Export authored definitions

Use the deployment directory containing the source workspace's state. Both
commands reuse cached Azure CLI sign-in and make no changes to the Fabric items:

```bash
.venv/bin/python -m fabric_test_drive.export_model --model <semantic-model-uuid>
.venv/bin/python -m fabric_test_drive.export_report --report <report-uuid>
```

Outputs are `templates/Northwind.SemanticModel/` and `templates/Northwind.Report/`.
For another workspace or without saved state, provide both `--tenant` and
`--workspace`. If authentication expires:

```bash
az login --tenant <tenant-uuid> --use-device-code --allow-no-subscriptions
```

The model exporter calls
`POST /v1/workspaces/{workspaceId}/semanticModels/{semanticModelId}/getDefinition?format=TMDL`.
The report exporter calls
`POST /v1/workspaces/{workspaceId}/reports/{reportId}/getDefinition`.
Both handle asynchronous exports and decode definition parts to files.
Reports retain the format supplied by Fabric: PBIR or PBIR-Legacy.

Existing directories are never overwritten. For template updates, export with
`--output` to a new directory, review the differences, and replace the old
template including removing stale files. Review definitions for sensitive
values, embedded content, and environment-specific references before committing:

```bash
git switch -c update-northwind-templates
git add templates/
git --no-pager diff --cached --stat
git --no-pager diff --cached
git commit -m "Update Northwind templates"
git push -u origin update-northwind-templates
```

Export does not automatically update deployed items. Test template changes in a
fresh workspace; recorded models and reports are reused, not overwritten.

## Deployment bindings

The model template's single OneLake source is rebound to the new workspace and
lakehouse IDs. Unexpected sources fail validation instead of being silently
retained. Keep table/column/measure names compatible with the report.

The report's `definition.pbir` dataset reference is replaced with
`semanticmodelid=<new-model-id>`, dropping the authored workspace and catalog
references. Other visual and static-resource files are preserved. Exported
`.platform` identities are not deployed.

Resource IDs, pending-operation markers, and completed stages are saved in
`.fabric-test-drive/state.json`. Successful reruns overwrite bronze/gold data;
interrupted runs reuse completed stages. See [Troubleshooting](troubleshooting.md)
before reconciling an ambiguous operation.

## Validation expectations

Run the unit suite for code changes. For deployment or template changes, also
exercise a fresh live workspace: complete without manual refresh, check positive
DAX results, inspect all report pages and filters, and test guarded cleanup.
A successful API create alone is not proof that data, model queries, or report
visuals work.

The complete Northwind path has been confirmed in a live tenant. That does not
establish compatibility with every capacity, tenant policy, region, or future
service change. This remains a demo, not a production deployment framework.
