// Azure AI Search service. SKU is chosen by the caller from the capacity calculation
// (`uv run fomo capacity`), never silently upgraded.

param searchServiceName string
param location string
param tags object

@allowed([
  'basic'
  'standard'
  'standard2'
  'standard3'
])
param sku string = 'basic'

@minValue(1)
param replicaCount int = 1

@minValue(1)
param partitionCount int = 1

@allowed([
  'disabled'
  'free'
  'standard'
])
param semanticSearch string = 'free'

resource search 'Microsoft.Search/searchServices@2025-05-01' = {
  name: searchServiceName
  location: location
  tags: tags
  sku: {
    name: sku
  }
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    replicaCount: replicaCount
    partitionCount: partitionCount
    hostingMode: 'Default'
    publicNetworkAccess: 'enabled'
    disableLocalAuth: true
    semanticSearch: semanticSearch
  }
}

output searchServiceId string = search.id
output searchServiceName string = search.name
output searchPrincipalId string = search.identity.principalId
output searchEndpoint string = 'https://${searchServiceName}.search.windows.net'
