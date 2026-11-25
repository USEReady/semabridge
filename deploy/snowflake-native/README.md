# SemaBridge Snowflake Native App (template)

**Status: template. Not yet validated against a Snowflake account.** Test in a
provider + consumer sandbox before listing.

The app runs inside the consumer's Snowflake account on Snowpark Container
Services: the same SemaBridge image as Azure (`SEMABRIDGE_DEPLOYMENT_PROFILE=snowflake_native`)
plus a PostgreSQL sidecar for SemaBridge's own metadata on a block volume.
Users are signed in by Snowflake (the app trusts `Sf-Context-Current-User`
via `/auth/platform-login`). Targets: the consumer's Snowflake account only.

## Provider steps
1. Build the image (`docker build -t semabridge:1.0.0 .` from the repo root,
   `--platform linux/amd64`) and push it and `postgres:16.4-alpine` to the
   package's image repository (`/semabridge_pkg/app_src/images`).
2. Upload `manifest.yml`, `setup_script.sql`, `service.yaml`, `README.md` to the
   application package stage; `ALTER APPLICATION PACKAGE ... ADD VERSION`.
3. Install in a consumer sandbox, grant the requested privileges,
   `CALL core.start_app();`, open the `ui` endpoint.

## Validate before listing
- Platform login: the `Sf-Context-Current-User` header reaches the app.
- The service restarts with data intact (block volumes).
- With the external access reference granted, Fabric extraction works; without it, PBIX upload works.
- Semantic views deploy using the app's session credentials (connection
  setup in the app still asks for warehouse/database; a follow-up can read
  `/snowflake/session/token` so no credentials are entered).
