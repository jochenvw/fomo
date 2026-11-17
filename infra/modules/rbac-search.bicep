// RBAC on the Azure AI Search service.

param searchServiceName string

@description('Foundry account + project system identities (used by the Foundry Azure AI Search tool).')
param foundryPrincipalIds array

@description('Array of {objectId, principalType}.')
param participants array = []

var searchIndexDataReader = '1407120a-92aa-4202-b7e9-c0e197c71c8f'
var searchIndexDataContributor = '8ebe5a00-799e-43f5-93ac-243d3dce84a7'
var searchServiceContributor = '7ca78c08-252a-4471-8644-bb5ff32d4ba0'

resource search 'Microsoft.Search/searchServices@2025-05-01' existing = {
  name: searchServiceName
}

// Docs name the account identity; the project identity is also granted because the
// connection is owned by the project. Both are read/query only on data.
resource foundryReadsIndex 'Microsoft.Authorization/roleAssignments@2022-04-01' = [
  for p in foundryPrincipalIds: {
    scope: search
    name: guid(search.id, p, searchIndexDataReader)
    properties: {
      principalId: p
      principalType: 'ServicePrincipal'
      roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', searchIndexDataReader)
    }
  }
]

resource foundryReadsService 'Microsoft.Authorization/roleAssignments@2022-04-01' = [
  for p in foundryPrincipalIds: {
    scope: search
    name: guid(search.id, p, searchServiceContributor)
    properties: {
      principalId: p
      principalType: 'ServicePrincipal'
      roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', searchServiceContributor)
    }
  }
]

resource pSearchSvc 'Microsoft.Authorization/roleAssignments@2022-04-01' = [
  for p in participants: {
    scope: search
    name: guid(search.id, p.objectId, searchServiceContributor)
    properties: {
      principalId: p.objectId
      principalType: p.principalType
      roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', searchServiceContributor)
    }
  }
]

resource pSearchData 'Microsoft.Authorization/roleAssignments@2022-04-01' = [
  for p in participants: {
    scope: search
    name: guid(search.id, p.objectId, searchIndexDataContributor)
    properties: {
      principalId: p.objectId
      principalType: p.principalType
      roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', searchIndexDataContributor)
    }
  }
]
