# Fabric Adapter Setup Guide

This guide covers the setup and configuration for using SemaBridge with Microsoft Fabric.

## Prerequisites

- Microsoft Fabric workspace (Premium or Fabric capacity)
- Azure AD tenant with app registration
- uv run 3.9+
- SemaBridge installed

## Connection Configuration

### Environment Variables

Set the following environment variables:

```bash
# Required
FABRIC_TENANT_ID=your_azure_tenant_id
FABRIC_CLIENT_ID=your_app_client_id
FABRIC_CLIENT_SECRET=your_app_client_secret
FABRIC_WORKSPACE_ID=your_fabric_workspace_id
```

## Azure App Registration

### Step 1: Create App Registration

1. Go to Azure Portal â†’ Azure Active Directory â†’ App registrations
2. Click "New registration"
3. Name: `SemaBridge-Fabric-Connector`
4. Supported account types: Single tenant
5. Click "Register"

### Step 2: Configure API Permissions

Add the following permissions:

| API | Permission | Type |
|-----|------------|------|
| Power BI Service | Dataset.ReadWrite.All | Application |
| Power BI Service | Workspace.Read.All | Application |
| Power BI Service | Tenant.Read.All | Application |
| Microsoft Fabric | Item.Read.All | Application |
| Microsoft Fabric | Item.ReadWrite.All | Application |

Then click "Grant admin consent".

> **Important:** The Fabric Items API (`/v1/workspaces/.../items`) requires the **Microsoft Fabric** API permissions, not just Power BI. Without `Item.Read.All`, the service principal will receive a 401 Unauthorized when listing semantic models even if the Power BI permissions are granted.

### Step 2b: Enable Service Principals in Fabric Tenant

Service principal access must be explicitly enabled in the Fabric admin portal:

1. Go to [Fabric Admin Portal](https://app.fabric.microsoft.com/admin-portal) → Tenant settings
2. Find **"Service principals can use Fabric APIs"** and enable it
3. Optionally restrict to a specific security group containing your app registration

Without this setting, all service principal calls to the Fabric API return 401 regardless of Azure AD permissions.

### Step 3: Create Client Secret

1. Go to "Certificates & secrets"
2. Click "New client secret"
3. Set expiration (recommended: 12-24 months)
4. Copy the secret value immediately (it won't be shown again)

### Step 4: Get IDs

- **Tenant ID**: Azure AD â†’ Overview â†’ Tenant ID
- **Client ID**: App registration â†’ Overview â†’ Application (client) ID
- **Workspace ID**: In Fabric, the workspace ID is in the URL: `app.powerbi.com/groups/{WORKSPACE_ID}`

## Workspace Setup

### Permissions

The app registration needs the following workspace permissions:

1. Go to Fabric workspace
2. Click settings (gear icon) â†’ Manage access
3. Add the app as "Contributor" or "Admin"

### Capacity Requirements

For semantic model operations:
- Premium (P-SKU) or Fabric (F-SKU) capacity required
- Some operations require Premium Per User (PPU)

## Rollback Configuration

### How Fabric Rollback Works

Unlike Snowflake, Fabric doesn't support DDL-based rollback. Instead:

1. **Model Versioning**: Full model definitions are stored as snapshots
2. **Re-deployment**: Rollback re-deploys the versioned model definition
3. **Replace Strategy**: Existing model is replaced with the target version

### Rollback Limitations

| Feature | Supported |
|---------|-----------|
| Semantic model rollback | âœ“ |
| Incremental rollback | âœ— (full model replacement) |
| Data rollback | âœ— (metadata only) |
| Refresh schedule preservation | âœ“ |

### Rollback Commands

```bash
# Preview rollback
uv run -m semabridge semantic rollback-preview --adapter fabric --to-version v20260119_103700_fabric_001

# Execute rollback (work in progress)
# Note: Fabric rollback uses model re-deployment
```

## Testing Procedures

### Test Connection

```bash
uv run -m semabridge validate
```

### Test Model Extraction

```bash
# List datasets in workspace
uv run -m semabridge list-projects

# Extract a specific dataset
uv run -m semabridge reverse-sync -d DATASET_ID
```

### Test Rollback

1. Deploy initial version tag:
```bash
uv run -m semabridge reverse-sync -d DATASET_ID --tag v1.0
```

2. Make changes in Fabric (manually or via sync)

3. Deploy new version:
```bash
uv run -m semabridge reverse-sync -d DATASET_ID --tag v2.0
```

4. Rollback to v1.0:
```bash
uv run -m semabridge rollback -d DATASET_ID --tag v1.0
```

## Controlling the Snowflake Semantic View Name

By default, SemaBridge uses the Fabric dataset's display name as the Snowflake semantic view name (with a `_SEMANTIC` suffix). For example, a dataset named `SalesModel` produces a view called `SalesModel_SEMANTIC`.

### Problem: Generic or Reserved Dataset Names

If your Fabric dataset has a generic name like `fabric`, `model`, or `snowflake`, the generated view name will be ambiguous (e.g. `fabric_SEMANTIC`). SemaBridge detects this and falls back to the dataset GUID, but the result is still not human-readable.

### Option 2: Override via `model_name` in Project Config

Add `model_name` to your project config YAML to control the Snowflake view name without renaming the Fabric dataset:

```yaml
# semabridge.yaml (or your project config)
model_name: "SalesAnalytics"   # → produces SalesAnalytics_SEMANTIC in Snowflake

source:
  type: fabric
  workspace_id: "your-workspace-id"

target:
  type: snowflake
```

You can also use `project_name` as an alias:

```yaml
project_name: "SalesAnalytics"
```

**Resolution order** (highest priority first):

1. `model_name` in project config YAML
2. `project_name` in project config YAML
3. Fabric dataset display name (from TMSL)
4. Fabric dataset GUID (fallback when display name is a reserved keyword)

> **Note:** Reserved keywords (`fabric`, `snowflake`, `pbix`, `databricks`, `model`) are automatically detected and will trigger a warning in the logs. Use `model_name` to set a meaningful name in these cases.

---

## Troubleshooting

### Common Issues

#### "AADSTS700016: Application not found"
- Verify tenant ID is correct
- Check if app registration is in the correct tenant

#### "Forbidden" or 403 Error
- Verify API permissions are granted
- Check if admin consent is granted
- Verify app has workspace access

#### 401 Unauthorized on Fabric API calls
This is the most common service-principal issue. Work through this checklist:

1. **Fabric tenant setting** — Go to Fabric Admin Portal → Tenant settings → enable **"Service principals can use Fabric APIs"**. Without this, all SP calls return 401.
2. **Azure AD permissions** — The app registration needs **Microsoft Fabric** API permissions (`Item.Read.All` / `Item.ReadWrite.All`), not just Power BI permissions. Grant admin consent after adding them.
3. **Workspace membership** — Add the service principal as Contributor or Admin in the Fabric workspace (Settings → Manage access).
4. **Permission type** — Fabric API permissions must be **Application** type (not Delegated) for service-principal (client-credentials) flow.

#### "InvalidRequest" on model operations
- Ensure workspace has Premium/Fabric capacity
- Check if dataset is in a valid state

#### "Model definition not found"
- Wait a few seconds after model creation
- Check the `/result` endpoint for async operations

### Debug Mode

Enable verbose logging:
```bash
uv run -m semabridge -v reverse-sync -d DATASET_ID
```

### View Logs

```bash
uv run -m semabridge semantic logs-show operations --lines 50
uv run -m semabridge semantic logs-show errors --lines 20
```

## Model Versioning Strategy

Since Fabric doesn't have native version control, SemaBridge implements:

### Local Storage

- Snapshots stored in `.semantic_metadata/snapshots/fabric/`
- Each snapshot contains full model definition (TMSL JSON)
- Schema hash for integrity verification

### Version Lineage

```
[v1.0] â†’ [v2.0] â†’ [v3.0] â†’ [v3.0_rollback_to_v1.0] â†’ [v4.0]
```

### Best Practices

1. **Always Tag**: Use descriptive version tags
2. **Pre-Deployment Backup**: SemaBridge automatically creates snapshots
3. **Test in Development**: Test rollback in dev workspace first
4. **Monitor Refresh**: Check if data refresh schedules are preserved after rollback

## Security Considerations

### Client Secret Rotation

- Rotate secrets every 12 months
- Update `FABRIC_CLIENT_SECRET` environment variable
- No code changes required

### Least Privilege

Grant only necessary permissions:
- For read-only: Use `Dataset.Read.All`
- For sync operations: Use `Dataset.ReadWrite.All`

### Audit Logging

SemaBridge logs all Fabric operations:
```bash
uv run -m semabridge semantic logs-show audit --lines 50
```

