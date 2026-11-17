// RBAC on the Storage account. Deterministic guid() names -> idempotent.

param storageAccountName string
param searchPrincipalId string

@description('Array of {objectId, principalType}.')
param participants array = []

@description('ABAC-constrained RBAC Administrator so participants can grant their hosted agent identity blob access.')
param allowParticipantAgentGrants bool = true

var storageBlobDataReader = '2a2b9908-6ea1-4ae2-8e65-a410df84e7d1'
var storageBlobDataContributor = 'ba92f5b4-2d11-453d-a403-e96b0029c9fe'
var rbacAdministrator = 'f58310d9-a9f6-439a-9e8d-f62e7b41a168'

var abacCondition = '((!(ActionMatches{\'Microsoft.Authorization/roleAssignments/write\'})) OR (@Request[Microsoft.Authorization/roleAssignments:RoleDefinitionId] ForAnyOfAnyValues:GuidEquals {${storageBlobDataContributor}} AND @Request[Microsoft.Authorization/roleAssignments:PrincipalType] StringEqualsIgnoreCase \'ServicePrincipal\')) AND ((!(ActionMatches{\'Microsoft.Authorization/roleAssignments/delete\'})) OR (@Resource[Microsoft.Authorization/roleAssignments:RoleDefinitionId] ForAnyOfAnyValues:GuidEquals {${storageBlobDataContributor}} AND @Resource[Microsoft.Authorization/roleAssignments:PrincipalType] StringEqualsIgnoreCase \'ServicePrincipal\'))'

resource storage 'Microsoft.Storage/storageAccounts@2025-01-01' existing = {
  name: storageAccountName
}

// Search indexer reads blobs + custom metadata.
resource searchReadsBlob 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (!empty(searchPrincipalId)) {
  scope: storage
  name: guid(storage.id, searchPrincipalId, storageBlobDataReader)
  properties: {
    principalId: searchPrincipalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', storageBlobDataReader)
  }
}

resource pBlob 'Microsoft.Authorization/roleAssignments@2022-04-01' = [
  for p in participants: {
    scope: storage
    name: guid(storage.id, p.objectId, storageBlobDataContributor)
    properties: {
      principalId: p.objectId
      principalType: p.principalType
      roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', storageBlobDataContributor)
    }
  }
]

resource pRbacAdmin 'Microsoft.Authorization/roleAssignments@2022-04-01' = [
  for p in (allowParticipantAgentGrants ? participants : []): {
    scope: storage
    name: guid(storage.id, p.objectId, rbacAdministrator)
    properties: {
      principalId: p.objectId
      principalType: p.principalType
      roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', rbacAdministrator)
      condition: abacCondition
      conditionVersion: '2.0'
    }
  }
]
