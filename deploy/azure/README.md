# Deploying SemaBridge to Azure

One resource group per client. The template creates the web app/API, a job
worker, PostgreSQL, a Key Vault, an Azure Files share and log collection.
Users sign in with Microsoft Entra ID before they reach the app.

## What gets created

| Resource | Purpose |
|---|---|
| Container App `<prefix>-api` | Web app + REST API. Entra ID sign-in at the front door. Probes on `/api/health/live` and `/api/health/ready`. |
| Container App `<prefix>-worker` | Executes syncs from the durable job queue. No public endpoint. |
| PostgreSQL flexible server | Single source of truth: projects, mappings, runs, jobs, shared state. TLS required. |
| Azure Files share → `/data` | Reports, run logs and caches shared by every replica. |
| Key Vault | Database URL, session signing key, credential encryption key, Entra client secret. Read by the app's managed identity. |
| Log Analytics | Container logs. |

## One-time steps

1. **Build and push the image** (from the repo root):
   ```sh
   az acr build -r <registry> -t semabridge:<version> .
   ```
2. **Let the app identity pull the image**: after the first deployment, grant
   the `identityPrincipalId` output the **AcrPull** role on the registry
   (the template cannot assign roles outside its resource group).
3. **Register the sign-in app** in Entra ID (App registrations → New):
   redirect URI `https://<prefix>-api.<region>.azurecontainerapps.io/.auth/login/aad/callback`
   (use the `appUrl` output), create a client secret.
4. **Create the secrets** the parameters file references (any Key Vault you
   control for bootstrapping):
   * `pg-admin-password`: strong password
   * `jwt-secret-key`: 48+ random characters (`openssl rand -base64 48`)
   * `credential-encryption-key`: 32+ random bytes
     (`python -c "import secrets; print(secrets.token_urlsafe(32))"`). Keep it:
     losing it makes every stored connection credential unreadable.
   * `entra-client-secret`: from step 3
5. **Deploy**:
   ```sh
   cp deploy/azure/main.parameters.example.json deploy/azure/main.parameters.json   # fill in
   az deployment group create -g <rg> -f deploy/azure/main.bicep -p @deploy/azure/main.parameters.json
   ```

The app runs its database migrations on start. On the Azure profile it
**refuses to start** if a migration fails or sign-in is disabled; check the
API app's log stream, fix, and restart the revision.

## Settings the template sets

| Variable | Value | Why |
|---|---|---|
| `SEMABRIDGE_DEPLOYMENT_PROFILE` | `azure` | Production rules: strict startup, no JSON state file, queue execution, no reset links in responses. |
| `SEMABRIDGE_PLATFORM_AUTH` | `true` (API) | Trust the Entra ID identity headers that Container Apps adds after sign-in. |
| `SEMABRIDGE_PLATFORM_ADMINS` | parameter | These emails get the admin role; everyone else is a normal user who owns their projects. |
| `SEMABRIDGE_EXECUTION_MODE` | `queue` | Syncs are durable jobs; a worker restart resumes them. |
| `SEMABRIDGE_EMBEDDED_WORKERS` | `0` (API) | The worker app executes jobs; the API only keeps the queue healthy. |

## Operating it

* **Scale**: raise `workerMinReplicas` / `maxReplicas` for more parallel syncs;
  each worker runs `SEMABRIDGE_WORKER_THREADS` jobs at once.
* **Upgrade**: push a new image tag, redeploy with the new `image`. Running
  syncs on a replaced worker are re-queued and resumed automatically.
* **Backups**: PostgreSQL keeps 14 days of point-in-time restore.
