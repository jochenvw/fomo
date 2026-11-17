// Foundry project -> Azure AI Search connection (keyless / AAD).
// The Foundry Azure AI Search tool resolves this connection by name.

param accountName string
param projectName string
param connectionName string
param searchServiceId string
param searchEndpoint string
param searchLocation string

resource account 'Microsoft.CognitiveServices/accounts@2025-06-01' existing = {
  name: accountName
}

resource project 'Microsoft.CognitiveServices/accounts/projects@2025-06-01' existing = {
  parent: account
  name: projectName
}

resource connection 'Microsoft.CognitiveServices/accounts/projects/connections@2025-06-01' = {
  parent: project
  name: connectionName
  properties: {
    category: 'CognitiveSearch'
    target: searchEndpoint
    authType: 'AAD'
    isSharedToAll: true
    metadata: {
      ApiType: 'Azure'
      ResourceId: searchServiceId
      location: searchLocation
    }
  }
}

output connectionId string = connection.id
output connectionName string = connection.name
