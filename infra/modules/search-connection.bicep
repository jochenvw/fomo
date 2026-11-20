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

// Smart RAG's OpenAPI search tool (agent-chosen document_type filter) authenticates with the
// project managed identity; that identity already holds Search Index Data Reader.
resource openapiConnection 'Microsoft.CognitiveServices/accounts/projects/connections@2025-06-01' = {
  parent: project
  name: '${connectionName}-openapi'
  properties: {
    category: 'OpenAPI'
    target: searchEndpoint
    #disable-next-line BCP036
    authType: 'ProjectManagedIdentity'
    audience: 'https://search.azure.com'
    isSharedToAll: true
    metadata: {
      type: 'openapi'
    }
  }
}

output connectionId string = connection.id
output connectionName string = connection.name
output openapiConnectionName string = openapiConnection.name
