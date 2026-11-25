// SemaBridge on Azure: client-ready, single resource group.
//
//   API container app (web app + REST API, Entra ID sign-in at the front door)
//   Worker container app (durable job queue; no ingress)
//   Azure Database for PostgreSQL flexible server (single source of truth)
//   Azure Files share mounted at /data (reports, run logs, caches)
//   Key Vault (secrets) + user-assigned managed identity (reads secrets, pulls image)
//   Log Analytics (container logs)
//
// Deploy:  az deployment group create -g <rg> -f deploy/azure/main.bicep -p @deploy/azure/main.parameters.json
// See deploy/azure/README.md for the one-time steps (image push, Entra app registration).

targetScope = 'resourceGroup'

@description('Short lowercase prefix for every resource name (3-11 chars).')
@minLength(3)
@maxLength(11)
param namePrefix string

param location string = resourceGroup().location

@description('Full image reference, e.g. myregistry.azurecr.io/semabridge:1.4.0')
param image string

@description('Registry login server of the image, e.g. myregistry.azurecr.io')
param registryServer string

@description('PostgreSQL administrator login (not "admin" / "postgres").')
param postgresAdminLogin string

@secure()
@description('PostgreSQL administrator password.')
param postgresAdminPassword string

@secure()
@description('Signing key for SemaBridge session tokens (48+ random characters).')
param jwtSecretKey string

@secure()
@description('Secret that encrypts stored connection credentials (32+ random bytes, e.g. secrets.token_urlsafe(32)).')
param credentialEncryptionKey string

@description('Entra ID app registration (client) id used for sign-in.')
param entraClientId string

@secure()
@description('Client secret of that app registration.')
param entraClientSecret string

@description('Entra ID tenant id that may sign in.')
param entraTenantId string = subscription().tenantId

@description('Comma-separated emails that are SemaBridge admins.')
param platformAdmins string = ''

@description('Number of always-on API replicas.')
@minValue(1)
param apiMinReplicas int = 1

@description('Number of always-on worker replicas.')
@minValue(1)
param workerMinReplicas int = 1

@description('PostgreSQL compute SKU.')
param postgresSkuName string = 'Standard_B2s'

@allowed(['Burstable', 'GeneralPurpose', 'MemoryOptimized'])
param postgresSkuTier string = 'Burstable'

var suffix = uniqueString(resourceGroup().id, namePrefix)
var dbName = 'semabridge'
var keyVaultName = take('${namePrefix}kv${suffix}', 24)
var storageName = take(toLower('${namePrefix}st${suffix}'), 24)
var shareName = 'semabridge-data'
var pgServerName = '${namePrefix}-pg-${suffix}'

// ---------------------------------------------------------------- identity
resource identity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: '${namePrefix}-id'
  location: location
}

// ---------------------------------------------------------------- logs
resource logs 'Microsoft.OperationalInsights/workspaces@2022-10-01' = {
  name: '${namePrefix}-logs'
  location: location
  properties: {
    sku: { name: 'PerGB2018' }
    retentionInDays: 30
  }
}

// ---------------------------------------------------------------- database
resource pg 'Microsoft.DBforPostgreSQL/flexibleServers@2023-06-01-preview' = {
  name: pgServerName
  location: location
  sku: {
    name: postgresSkuName
    tier: postgresSkuTier
  }
  properties: {
    version: '16'
    administratorLogin: postgresAdminLogin
    administratorLoginPassword: postgresAdminPassword
    storage: { storageSizeGB: 32 }
    backup: {
      backupRetentionDays: 14
      geoRedundantBackup: 'Disabled'
    }
    highAvailability: { mode: 'Disabled' }
  }
}

resource pgDb 'Microsoft.DBforPostgreSQL/flexibleServers/databases@2023-06-01-preview' = {
  parent: pg
  name: dbName
  properties: {
    charset: 'UTF8'
    collation: 'en_US.utf8'
  }
}

// Container Apps reach the server over its public endpoint; TLS is required.
resource pgAllowAzure 'Microsoft.DBforPostgreSQL/flexibleServers/firewallRules@2023-06-01-preview' = {
  parent: pg
  name: 'AllowAzureServices'
  properties: {
    startIpAddress: '0.0.0.0'
    endIpAddress: '0.0.0.0'
  }
}

// ---------------------------------------------------------------- storage
resource storage 'Microsoft.Storage/storageAccounts@2023-01-01' = {
  name: storageName
  location: location
  sku: { name: 'Standard_LRS' }
  kind: 'StorageV2'
  properties: {
    minimumTlsVersion: 'TLS1_2'
    allowBlobPublicAccess: false
    supportsHttpsTrafficOnly: true
  }
}

resource fileService 'Microsoft.Storage/storageAccounts/fileServices@2023-01-01' = {
  parent: storage
  name: 'default'
}

resource share 'Microsoft.Storage/storageAccounts/fileServices/shares@2023-01-01' = {
  parent: fileService
  name: shareName
  properties: { shareQuota: 100 }
}

// ---------------------------------------------------------------- secrets
resource vault 'Microsoft.KeyVault/vaults@2023-07-01' = {
  name: keyVaultName
  location: location
  properties: {
    tenantId: subscription().tenantId
    sku: {
      family: 'A'
      name: 'standard'
    }
    enableRbacAuthorization: true
    enableSoftDelete: true
    softDeleteRetentionInDays: 30
    enablePurgeProtection: true
  }
}

var databaseUrl = 'postgresql+psycopg2://${postgresAdminLogin}:${uriComponent(postgresAdminPassword)}@${pg.properties.fullyQualifiedDomainName}:5432/${dbName}?sslmode=require'

resource secretDb 'Microsoft.KeyVault/vaults/secrets@2023-07-01' = {
  parent: vault
  name: 'database-url'
  properties: { value: databaseUrl }
}

resource secretJwt 'Microsoft.KeyVault/vaults/secrets@2023-07-01' = {
  parent: vault
  name: 'jwt-secret-key'
  properties: { value: jwtSecretKey }
}

resource secretEnc 'Microsoft.KeyVault/vaults/secrets@2023-07-01' = {
  parent: vault
  name: 'credential-encryption-key'
  properties: { value: credentialEncryptionKey }
}

resource secretEntra 'Microsoft.KeyVault/vaults/secrets@2023-07-01' = {
  parent: vault
  name: 'entra-client-secret'
  properties: { value: entraClientSecret }
}

// Key Vault Secrets User for the app identity.
resource kvSecretsUser 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: vault
  name: guid(vault.id, identity.id, 'kv-secrets-user')
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '4633458b-17de-408a-b874-0445c86b69e6')
    principalId: identity.properties.principalId
    principalType: 'ServicePrincipal'
  }
}

// ---------------------------------------------------------------- apps
resource env 'Microsoft.App/managedEnvironments@2024-03-01' = {
  name: '${namePrefix}-env'
  location: location
  properties: {
    appLogsConfiguration: {
      destination: 'log-analytics'
      logAnalyticsConfiguration: {
        customerId: logs.properties.customerId
        sharedKey: logs.listKeys().primarySharedKey
      }
    }
  }
}

resource envStorage 'Microsoft.App/managedEnvironments/storages@2024-03-01' = {
  parent: env
  name: 'semabridgedata'
  properties: {
    azureFile: {
      accountName: storage.name
      accountKey: storage.listKeys().keys[0].value
      shareName: shareName
      accessMode: 'ReadWrite'
    }
  }
}

var secrets = [
  {
    name: 'database-url'
    keyVaultUrl: secretDb.properties.secretUri
    identity: identity.id
  }
  {
    name: 'jwt-secret-key'
    keyVaultUrl: secretJwt.properties.secretUri
    identity: identity.id
  }
  {
    name: 'credential-encryption-key'
    keyVaultUrl: secretEnc.properties.secretUri
    identity: identity.id
  }
  {
    name: 'entra-client-secret'
    keyVaultUrl: secretEntra.properties.secretUri
    identity: identity.id
  }
]

var commonEnv = [
  { name: 'SEMABRIDGE_DEPLOYMENT_PROFILE', value: 'azure' }
  { name: 'SEMABRIDGE_DATABASE_URL', secretRef: 'database-url' }
  { name: 'SEMABRIDGE_DB_BACKEND', value: 'orm' }
  { name: 'AUTH_ENABLED', value: 'true' }
  { name: 'JWT_SECRET_KEY', secretRef: 'jwt-secret-key' }
  { name: 'SEMABRIDGE_ENCRYPTION_KEY', secretRef: 'credential-encryption-key' }
  { name: 'SEMABRIDGE_EXECUTION_MODE', value: 'queue' }
  { name: 'SEMABRIDGE_DISABLE_DOTENV', value: '1' }
  { name: 'COOKIE_SECURE', value: 'true' }
]

var volumes = [
  {
    name: 'data'
    storageType: 'AzureFile'
    storageName: envStorage.name
  }
]

var volumeMounts = [
  {
    volumeName: 'data'
    mountPath: '/data'
  }
]

resource api 'Microsoft.App/containerApps@2024-03-01' = {
  name: '${namePrefix}-api'
  location: location
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: { '${identity.id}': {} }
  }
  dependsOn: [ kvSecretsUser, pgDb, pgAllowAzure ]
  properties: {
    managedEnvironmentId: env.id
    configuration: {
      ingress: {
        external: true
        targetPort: 8002
        transport: 'auto'
        allowInsecure: false
      }
      registries: [
        {
          server: registryServer
          identity: identity.id
        }
      ]
      secrets: secrets
    }
    template: {
      containers: [
        {
          name: 'api'
          image: image
          args: [ 'api' ]
          resources: {
            cpu: json('1.0')
            memory: '2Gi'
          }
          env: concat(commonEnv, [
            // Entra ID authenticates every user at the front door; the
            // app trusts the forwarded identity headers.
            { name: 'SEMABRIDGE_PLATFORM_AUTH', value: 'true' }
            { name: 'SEMABRIDGE_PLATFORM_ADMINS', value: platformAdmins }
            // Jobs run in the worker app; this process only maintains the queue.
            { name: 'SEMABRIDGE_EMBEDDED_WORKERS', value: '0' }
            { name: 'SEMABRIDGE_API_PROCESSES', value: '2' }
          ])
          probes: [
            {
              type: 'Liveness'
              httpGet: {
                path: '/api/health/live'
                port: 8002
              }
              periodSeconds: 30
            }
            {
              type: 'Readiness'
              httpGet: {
                path: '/api/health/ready'
                port: 8002
              }
              periodSeconds: 15
            }
          ]
          volumeMounts: volumeMounts
        }
      ]
      scale: {
        minReplicas: apiMinReplicas
        maxReplicas: 5
      }
      volumes: volumes
    }
  }
}

resource apiAuth 'Microsoft.App/containerApps/authConfigs@2024-03-01' = {
  parent: api
  name: 'current'
  properties: {
    platform: { enabled: true }
    globalValidation: {
      unauthenticatedClientAction: 'RedirectToLoginPage'
      redirectToProvider: 'azureactivedirectory'
      // Platform health probes must not be redirected to sign-in.
      excludedPaths: [ '/api/health/live', '/api/health/ready' ]
    }
    identityProviders: {
      azureActiveDirectory: {
        enabled: true
        registration: {
          clientId: entraClientId
          clientSecretSettingName: 'entra-client-secret'
          openIdIssuer: '${environment().authentication.loginEndpoint}${entraTenantId}/v2.0'
        }
        validation: {
          allowedAudiences: [ 'api://${entraClientId}', entraClientId ]
        }
      }
    }
  }
}

resource worker 'Microsoft.App/containerApps@2024-03-01' = {
  name: '${namePrefix}-worker'
  location: location
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: { '${identity.id}': {} }
  }
  dependsOn: [ kvSecretsUser, pgDb, pgAllowAzure ]
  properties: {
    managedEnvironmentId: env.id
    configuration: {
      registries: [
        {
          server: registryServer
          identity: identity.id
        }
      ]
      secrets: secrets
    }
    template: {
      containers: [
        {
          name: 'worker'
          image: image
          args: [ 'worker' ]
          resources: {
            cpu: json('2.0')
            memory: '4Gi'
          }
          env: concat(commonEnv, [
            { name: 'SEMABRIDGE_WORKER_THREADS', value: '4' }
          ])
          volumeMounts: volumeMounts
        }
      ]
      scale: {
        minReplicas: workerMinReplicas
        maxReplicas: 3
      }
      volumes: volumes
    }
  }
}

output appUrl string = 'https://${api.properties.configuration.ingress.fqdn}'
output identityPrincipalId string = identity.properties.principalId
output identityClientId string = identity.properties.clientId
output keyVaultName string = vault.name
output postgresHost string = pg.properties.fullyQualifiedDomainName
