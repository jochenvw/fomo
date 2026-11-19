// FOMO metadata-agent workshop - shared infrastructure for the Contoso Oil demo.
// Resource-group scope. Creates (or reuses) Foundry account+project, 3 model
// deployments, Blob storage, Azure AI Search, the project->Search connection and RBAC.
// Per-participant data-plane objects (container, indexes, indexers, agents) are created
// by `uv run fomo provision`, not by Bicep.

targetScope = 'resourceGroup'

@description('Azure region. Must offer Microsoft-Decision-1.1, gpt-5-mini, text-embedding-3-small and hosted agents.')
param location string = resourceGroup().location

@description('Short name used to derive resource names.')
@minLength(3)
@maxLength(12)
param prefix string = 'fomo'

@description('Foundry project name. Pin the original name when redeploying a legacy environment.')
param projectName string = 'fomo'

@description('Workshop/event id appended to tags (e.g. 2026-05-ams).')
param eventId string = 'dev'

@description('Search SKU from the capacity calculation. basic ~7 participants, standard ~25, standard2 ~100 (5 objects each).')
@allowed([
  'basic'
  'standard'
  'standard2'
  'standard3'
])
param searchSku string = 'basic'

param searchReplicaCount int = 1

@description('Chat model deployment.')
param chatModel object = {
  name: 'gpt-5-mini'
  format: 'OpenAI'
  model: 'gpt-5-mini'
  version: '2025-08-07'
  sku: 'GlobalStandard'
  capacity: 100
}

@description('Embedding model deployment (1536 dims).')
param embeddingModel object = {
  name: 'text-embedding-3-small'
  format: 'OpenAI'
  model: 'text-embedding-3-small'
  version: '1'
  sku: 'GlobalStandard'
  capacity: 150
}

@description('Microsoft-Decision-1.1 deployment (model version 1).')
param decisionModel object = {
  name: 'decision-1-1'
  format: 'Microsoft'
  model: 'Microsoft-Decision-1.1'
  version: '1'
  sku: 'GlobalStandard'
  capacity: 50
}

@description('Facilitator evaluation only (`fomo eval-ladder`): judge from a different model family than the agents. Participants never need it.')
param deployEvaluationJudge bool = false

param evaluationJudgeModel object = {
  name: 'gpt-5.4'
  format: 'OpenAI'
  model: 'gpt-5.4'
  version: '2026-03-05'
  sku: 'GlobalStandard'
  capacity: 100
}

@description('Participants / facilitator principals: array of {objectId, principalType: User|Group|ServicePrincipal}.')
param participants array = []

param allowParticipantAgentGrants bool = true

param searchConnectionName string = 'fomo-search'

@description('Optional explicit names (some RPs reject generated names that happen to contain reserved substrings).')
param foundryAccountNameOverride string = ''
param storageAccountNameOverride string = ''
param searchServiceNameOverride string = ''

@description('''public = Blob public endpoint, Entra ID only (no shared key, no anonymous access). Required for the
hosted Metadata Miner: hosted agents reach storage over public egress unless the Foundry account was created
with VNet injection. perimeter = Blob behind an enforced Network Security Perimeter; Search and local runs work,
the hosted miner is blocked (HTTP 403 "not authorized by network security perimeter"). See docs/troubleshooting.md.''')
@allowed(['perimeter', 'public'])
param storageNetworkMode string = 'public'

@description('''Extra tags on the storage account only. Use when a governance Modify policy forces Storage
publicNetworkAccess=Disabled and documents an exclusion tag (MCAPS: {SecurityControl: 'Ignore'}). Facilitator decision.''')
param storagePolicyExemptionTags object = {}

@description('perimeter mode: public egress CIDRs of facilitator/participant machines allowed to reach Blob.')
param allowedIpPrefixes array = []

var suffix = uniqueString(subscription().id, resourceGroup().id, prefix)
var accountName = empty(foundryAccountNameOverride) ? toLower('${prefix}-${suffix}') : foundryAccountNameOverride
var storageAccountName = empty(storageAccountNameOverride) ? toLower(take('${replace(prefix, '-', '')}st${suffix}', 24)) : storageAccountNameOverride
var searchServiceName = empty(searchServiceNameOverride) ? toLower('${prefix}-srch-${suffix}') : searchServiceNameOverride
var tags = {
  workshop: 'fomo'
  event: eventId
  managedBy: 'fomo-cli'
}

module foundry 'modules/foundry.bicep' = {
  name: 'foundry'
  params: {
    accountName: accountName
    projectName: projectName
    location: location
    tags: tags
  }
}

module models 'modules/models.bicep' = {
  name: 'models'
  params: {
    accountName: foundry.outputs.accountName
    deployments: concat([
      chatModel
      embeddingModel
      decisionModel
    ], deployEvaluationJudge ? [evaluationJudgeModel] : [])
  }
}

module storage 'modules/storage.bicep' = {
  name: 'storage'
  params: {
    storageAccountName: storageAccountName
    location: location
    tags: union(tags, storagePolicyExemptionTags)
    publicNetworkAccess: storageNetworkMode == 'perimeter' ? 'Disabled' : 'Enabled'
  }
}

module perimeter 'modules/perimeter.bicep' = if (storageNetworkMode == 'perimeter') {
  name: 'perimeter'
  params: {
    perimeterName: '${prefix}-nsp-${suffix}'
    location: location
    tags: tags
    storageAccountId: storage.outputs.storageAccountId
    allowedIpPrefixes: allowedIpPrefixes
  }
}

module search 'modules/search.bicep' = {
  name: 'search'
  params: {
    searchServiceName: searchServiceName
    location: location
    tags: tags
    sku: searchSku
    replicaCount: searchReplicaCount
  }
}

module connection 'modules/search-connection.bicep' = {
  name: 'search-connection'
  params: {
    accountName: foundry.outputs.accountName
    projectName: foundry.outputs.projectName
    connectionName: searchConnectionName
    searchServiceId: search.outputs.searchServiceId
    searchEndpoint: search.outputs.searchEndpoint
    searchLocation: location
  }
}

module rbacStorage 'modules/rbac-storage.bicep' = {
  name: 'rbac-storage'
  params: {
    storageAccountName: storage.outputs.storageAccountName
    searchPrincipalId: search.outputs.searchPrincipalId
    participants: participants
    allowParticipantAgentGrants: allowParticipantAgentGrants
  }
}

module rbacSearch 'modules/rbac-search.bicep' = {
  name: 'rbac-search'
  params: {
    searchServiceName: search.outputs.searchServiceName
    foundryPrincipalIds: [
      foundry.outputs.accountPrincipalId
      foundry.outputs.projectPrincipalId
    ]
    participants: participants
  }
}

module rbacFoundry 'modules/rbac-foundry.bicep' = {
  name: 'rbac-foundry'
  params: {
    accountName: foundry.outputs.accountName
    projectName: foundry.outputs.projectName
    searchPrincipalId: search.outputs.searchPrincipalId
    projectPrincipalId: foundry.outputs.projectPrincipalId
    participants: participants
    allowParticipantAgentGrants: allowParticipantAgentGrants
  }
}

output AZURE_LOCATION string = location
output FOUNDRY_ACCOUNT_NAME string = foundry.outputs.accountName
output FOUNDRY_ACCOUNT_ENDPOINT string = foundry.outputs.accountEndpoint
output FOUNDRY_OPENAI_ENDPOINT string = foundry.outputs.openAiEndpoint
output FOUNDRY_PROJECT_NAME string = foundry.outputs.projectName
output FOUNDRY_PROJECT_ENDPOINT string = foundry.outputs.projectEndpoint
output CHAT_DEPLOYMENT string = chatModel.name
output EMBEDDING_DEPLOYMENT string = embeddingModel.name
output DECISION_DEPLOYMENT string = decisionModel.name
output STORAGE_ACCOUNT_NAME string = storage.outputs.storageAccountName
output STORAGE_ACCOUNT_ID string = storage.outputs.storageAccountId
output STORAGE_BLOB_ENDPOINT string = storage.outputs.blobEndpoint
output STORAGE_NETWORK_MODE string = storageNetworkMode
output SEARCH_SERVICE_NAME string = search.outputs.searchServiceName
output SEARCH_ENDPOINT string = search.outputs.searchEndpoint
output SEARCH_SKU string = searchSku
output SEARCH_CONNECTION_NAME string = connection.outputs.connectionName
