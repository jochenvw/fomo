// Reuse-existing mode: customer-provisioned Foundry account/project, Storage and Search,
// possibly in different resource groups (same subscription or explicit subscription id).
// Creates ONLY: the project->Search connection, optional missing model deployments and
// RBAC. Never changes network, auth or SKU settings of the existing resources.
// Deploy at the resource group that contains the Foundry account.

targetScope = 'resourceGroup'

param foundryAccountName string
param foundryProjectName string

param storageAccountName string
param storageResourceGroup string = resourceGroup().name
param storageSubscriptionId string = subscription().subscriptionId

param searchServiceName string
param searchResourceGroup string = resourceGroup().name
param searchSubscriptionId string = subscription().subscriptionId

param searchConnectionName string = 'contoso-search'

@description('Model deployments to create if the facilitator lacks them; [] = assume they exist (verified by preflight).')
param createModelDeployments array = []

param participants array = []
param allowParticipantAgentGrants bool = true

@description('Set false when the caller cannot write role assignments; preflight then reports the grants an admin must make.')
param assignRoles bool = true

resource account 'Microsoft.CognitiveServices/accounts@2025-06-01' existing = {
  name: foundryAccountName
}

resource project 'Microsoft.CognitiveServices/accounts/projects@2025-06-01' existing = {
  parent: account
  name: foundryProjectName
}

resource search 'Microsoft.Search/searchServices@2025-05-01' existing = {
  name: searchServiceName
  scope: resourceGroup(searchSubscriptionId, searchResourceGroup)
}

resource storage 'Microsoft.Storage/storageAccounts@2025-01-01' existing = {
  name: storageAccountName
  scope: resourceGroup(storageSubscriptionId, storageResourceGroup)
}

module models 'modules/models.bicep' = if (!empty(createModelDeployments)) {
  name: 'models'
  params: {
    accountName: foundryAccountName
    deployments: createModelDeployments
  }
}

module connection 'modules/search-connection.bicep' = {
  name: 'search-connection'
  params: {
    accountName: foundryAccountName
    projectName: foundryProjectName
    connectionName: searchConnectionName
    searchServiceId: search.id
    searchEndpoint: 'https://${searchServiceName}.search.windows.net'
    searchLocation: search.location
  }
}

var searchPrincipalId = search.identity.?principalId ?? ''
var accountPrincipalId = account.identity.?principalId ?? ''
var projectPrincipalId = project.identity.?principalId ?? ''

module rbacStorage 'modules/rbac-storage.bicep' = if (assignRoles) {
  name: 'rbac-storage'
  scope: resourceGroup(storageSubscriptionId, storageResourceGroup)
  params: {
    storageAccountName: storageAccountName
    searchPrincipalId: searchPrincipalId
    participants: participants
    allowParticipantAgentGrants: allowParticipantAgentGrants
  }
}

module rbacSearch 'modules/rbac-search.bicep' = if (assignRoles) {
  name: 'rbac-search'
  scope: resourceGroup(searchSubscriptionId, searchResourceGroup)
  params: {
    searchServiceName: searchServiceName
    foundryPrincipalIds: filter([accountPrincipalId, projectPrincipalId], id => !empty(id))
    participants: participants
  }
}

module rbacFoundry 'modules/rbac-foundry.bicep' = if (assignRoles) {
  name: 'rbac-foundry'
  params: {
    accountName: foundryAccountName
    projectName: foundryProjectName
    searchPrincipalId: searchPrincipalId
    projectPrincipalId: projectPrincipalId
    participants: participants
    allowParticipantAgentGrants: allowParticipantAgentGrants
  }
}

output FOUNDRY_ACCOUNT_NAME string = foundryAccountName
output FOUNDRY_ACCOUNT_ENDPOINT string = 'https://${account.properties.customSubDomainName}.services.ai.azure.com'
output FOUNDRY_OPENAI_ENDPOINT string = 'https://${account.properties.customSubDomainName}.openai.azure.com'
output FOUNDRY_PROJECT_NAME string = foundryProjectName
output FOUNDRY_PROJECT_ENDPOINT string = 'https://${account.properties.customSubDomainName}.services.ai.azure.com/api/projects/${foundryProjectName}'
output STORAGE_ACCOUNT_NAME string = storageAccountName
output STORAGE_ACCOUNT_ID string = storage.id
output STORAGE_BLOB_ENDPOINT string = storage.properties.primaryEndpoints.blob
output SEARCH_SERVICE_NAME string = searchServiceName
output SEARCH_ENDPOINT string = 'https://${searchServiceName}.search.windows.net'
output SEARCH_CONNECTION_NAME string = connection.outputs.connectionName
