// RBAC on the Foundry account / project.

param accountName string
param projectName string

param searchPrincipalId string
param projectPrincipalId string

@description('Array of {objectId, principalType}.')
param participants array = []

@description('ABAC-constrained RBAC Administrator so participants can grant their hosted agent identity Foundry User (decision API data plane) and nothing else.')
param allowParticipantAgentGrants bool = true

var cognitiveServicesOpenAIUser = '5e0bd9bd-7b93-4f28-af87-19fc36ad61bd'
var foundryUser = '53ca6127-db72-4b80-b1b0-d745d6d5456d'
var foundryProjectManager = 'eadc314b-1a2d-4efa-be10-5d325db5065e'
var rbacAdministrator = 'f58310d9-a9f6-439a-9e8d-f62e7b41a168'

var abacCondition = '((!(ActionMatches{\'Microsoft.Authorization/roleAssignments/write\'})) OR (@Request[Microsoft.Authorization/roleAssignments:RoleDefinitionId] ForAnyOfAnyValues:GuidEquals {${foundryUser}} AND @Request[Microsoft.Authorization/roleAssignments:PrincipalType] StringEqualsIgnoreCase \'ServicePrincipal\')) AND ((!(ActionMatches{\'Microsoft.Authorization/roleAssignments/delete\'})) OR (@Resource[Microsoft.Authorization/roleAssignments:RoleDefinitionId] ForAnyOfAnyValues:GuidEquals {${foundryUser}} AND @Resource[Microsoft.Authorization/roleAssignments:PrincipalType] StringEqualsIgnoreCase \'ServicePrincipal\'))'

resource account 'Microsoft.CognitiveServices/accounts@2025-06-01' existing = {
  name: accountName
}

resource project 'Microsoft.CognitiveServices/accounts/projects@2025-06-01' existing = {
  parent: account
  name: projectName
}

// Search embedding skill + query vectorizer call the embedding deployment.
resource searchCallsEmbeddings 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (!empty(searchPrincipalId)) {
  scope: account
  name: guid(account.id, searchPrincipalId, cognitiveServicesOpenAIUser)
  properties: {
    principalId: searchPrincipalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', cognitiveServicesOpenAIUser)
  }
}

resource projectUsesAccount 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (!empty(projectPrincipalId)) {
  scope: account
  name: guid(account.id, projectPrincipalId, foundryUser)
  properties: {
    principalId: projectPrincipalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', foundryUser)
  }
}

// Foundry User carries Microsoft.CognitiveServices/* data actions, which include the
// decision API action Microsoft.CognitiveServices/accounts/AIServices/providers/action.
resource pFoundryUser 'Microsoft.Authorization/roleAssignments@2022-04-01' = [
  for p in participants: {
    scope: account
    name: guid(account.id, p.objectId, foundryUser)
    properties: {
      principalId: p.objectId
      principalType: p.principalType
      roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', foundryUser)
    }
  }
]

// Creating hosted agents (create_version_from_code) in the project.
resource pProjectManager 'Microsoft.Authorization/roleAssignments@2022-04-01' = [
  for p in participants: {
    scope: project
    name: guid(project.id, p.objectId, foundryProjectManager)
    properties: {
      principalId: p.objectId
      principalType: p.principalType
      roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', foundryProjectManager)
    }
  }
]

resource pRbacAdmin 'Microsoft.Authorization/roleAssignments@2022-04-01' = [
  for p in (allowParticipantAgentGrants ? participants : []): {
    scope: account
    name: guid(account.id, p.objectId, rbacAdministrator)
    properties: {
      principalId: p.objectId
      principalType: p.principalType
      roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', rbacAdministrator)
      condition: abacCondition
      conditionVersion: '2.0'
    }
  }
]
