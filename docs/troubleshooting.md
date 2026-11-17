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

### Model refresh fails and the report is missing

Initial model refresh failures have been observed during deployment, even when
the lakehouse and gold tables were created successfully. **The report is not
created until model refresh and query verification succeed.** A missing report
at this point does not mean you need to start a new deployment.

1. Open the **newly created deployment workspace** in Fabric.
2. Find the **Northwind Model** semantic model, select its **...** menu, and choose
   **Refresh now**. Wait for any existing refresh to finish before starting another.
3. Check **Refresh history** and wait until the manual refresh shows **Completed**.
   If it fails, inspect the error and resolve any source-access or permission
   problem before proceeding.
4. Return to the **same Codespace and deployment directory**, keeping
   `.fabric-test-drive/state.json` intact, and rerun:

   ```bash
   .venv/bin/fabric-test-drive deploy --skip-login
   ```

The launcher resumes from saved state, reuses the existing workspace, lakehouse,
model, and completed data stages, and proceeds to report creation after its model
checks pass. It may submit another automated refresh if the saved refresh attempt
failed; it does not automatically adopt a separate manual refresh as that attempt.
Allow the command to finish and open the printed report link.

**Do not run cleanup, delete state, or create a replacement workspace to recover
from this refresh failure.** If the error instead mentions an unresolved
submission (`pending_job`), follow
[Unresolved create or job submission](#unresolved-create-or-job-submission)
before retrying.

### Other refresh, query, and rendering errors

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

## Optional Fabric App

The launcher reuses Azure CLI authentication for the app. You should not need
to run `rayfin login`, enable plaintext token storage, or copy a token into your
terminal. If an older launcher opens a browser and then stalls at a localhost
callback or reports that no OS keychain is available, stop it with Ctrl+C and
update the checkout before retrying. Keep the saved deployment state.

If the app prerequisite check reports missing or unsupported Node.js, use
Node 24 and npm. New Codespaces include both; existing Codespaces need them
installed or a container rebuild after preserving state. The standard deployment
does not need Node: select No at the initial question or use `--without-app`.

The app is deployed only after the model and report pass verification.
If it fails before remote submission, correct the reported problem and rerun
`deploy --skip-login --with-app`. If state contains `pending_app`, a remote
operation may have succeeded or may still be running. Do not retry blindly or
delete the marker: inspect the recorded workspace and the staged Rayfin registry
at `.fabric-test-drive/app/rayfin/.deployments.json` and reconcile the outcome
before further deployment. The working core environment is retained.

An existing app with the same name but no matching recorded ID is not reused
automatically. Inspect it instead of approving an overwrite. Once deployed,
open the app's hosting URL and check authentication, data, charts, and filters;
deployment success alone does not verify the browser experience.

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
