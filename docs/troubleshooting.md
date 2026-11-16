# Troubleshooting

Run commands from the original deployment directory. Preserve
`.fabric-test-drive/state.json` until cleanup finishes. Do not paste passwords,
tokens, or authentication caches into issues or support requests.

## Sign-in or permissions

Use `--skip-login` to reuse an existing Azure CLI session. If the session has
expired, rerun without that flag. Device-code authentication can be blocked by
Conditional Access; your administrator must approve a supported sign-in path.
The launcher does not bypass tenant policy or administrator consent.

A successful preflight is not proof of permission to create workspaces, run the
connector, execute Spark, or call all Power BI APIs. Check the failed operation's
error and request ID. The account must retain workspace and source-table access
and own the semantic model.

## Capacity selection

Select an accessible, active capacity supporting lakehouses, Spark, and Power BI.
The list comes from the API and may include entries unsuitable for this demo.
Power BI Premium Per User alone is not a Fabric capacity. Check capacity state,
access, workload support, throttling, and tenant settings with your administrator.
Capacity shortages can make jobs wait or fail.

## Public Northwind service unavailable

Preflight stops before creating resources if the sample source cannot be read.
This may be transient or caused by the Codespace's network, DNS, or TLS settings.
Check from the same terminal:

```bash
curl -i --max-time 30 \
  'https://services.odata.org/V4/Northwind/Northwind.svc/Customers?$top=1&$select=CustomerID'
```

Expect HTTP 200 and a nonempty `value` array. If the source recovers, rerun deploy
from the same directory. There is no bundled-data fallback.

The OData connector may advertise only `NotEncrypted` as its connection API
setting. The configured source still uses HTTPS; the launcher refuses HTTP.

## Deployment cancelled

At `Type deploy to continue:`, type **`deploy`**, not `continue` or `yes`.
Cancellation at this prompt creates no resources.

## Refresh, DAX, or report problems

Deployment sets the model's target storage mode to `PremiumFiles`, submits a
transactional full refresh with up to two service-side retries (five minutes per
attempt), and polls completion. A target storage setting alone does not establish
readiness. The overall refresh wait is bounded to 30 minutes per invocation.

If it fails, inspect the model's refresh history for the actual error. Check that
the model owner can read the gold tables and that the source connection targets
the deployment's lakehouse. An initial table/access error may be transient, but
do not assume every permission error is a readiness delay.

A known failed refresh can be retried by running deploy again. Active refreshes
are polled by saved ID rather than submitted again; timeouts preserve that ID.
If refresh completed but the DAX check failed, completed refresh work is reused.
Check the **Dataset Execute Queries REST API** tenant setting and Build/read
permission. HTTP 200 responses with DAX errors still fail verification.

To check the model independently in **DAX query view**, run:

```DAX
EVALUATE ROW("Sales", [Total Sales], "Orders", [Order Count])
```

Confirm you opened the newly deployed model, not an older one. Notebook code is
PySpark, DAX query view uses DAX, and terminal commands use Bash; they are not
interchangeable.

The report's dataset ID is checked automatically, but visual rendering is not.
Open every page and test filters. A recorded report with an unexpected binding
causes deployment to stop rather than silently overwrite your changes.

## Unresolved create or job submission

If a server accepted a request but its response was lost, retrying blindly could
create duplicate resources or jobs. The launcher records `pending_create` or
`pending_job` and blocks further writes and cleanup.

Inspect Fabric before changing state. For a create, confirm that no operation
is still running and remove any unrecorded resource before removing only the
unresolved marker. For a job, inspect run or refresh history, wait for or cancel
active work, and reconcile its recorded ID and status before clearing the marker.
Do not delete the whole state file or clear a marker while work may still run.

Fabric long-running operations use `x-ms-operation-id` and Power BI refreshes
use `x-ms-request-id` when available. Polling stays on their respective API hosts;
the launcher does not send tokens to arbitrary regional URLs.

## Cleanup stops

Verify you are in the correct directory and tenant. Cleanup checks workspace
ownership/capacity and the connection name, requires the workspace name as
confirmation, and refuses while recorded work is active.

An explicitly missing workspace is marked deleted and connection cleanup
continues. A missing connection, an ownership mismatch, unresolved operation, or
another API error requires inspection. Finish cleanup before deploying again
if deletion only partially completed.

## Legacy deployment state

Older non-schema lakehouses cannot be converted in place by this launcher.
Keep their state for cleanup. Use a separate directory and workspace name for
a new deployment, or clean up the old environment first if you no longer need it.

## Reporting an issue

Include the command, current commit (`git log -1 --oneline`), failed stage,
sanitized terminal error, and relevant Fabric request ID or refresh-history
error. Review all output before sharing. Do not attach state or sign-in caches.
