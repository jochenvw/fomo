// Microsoft Foundry account (AIServices kind, new resource model) + one project.
// No hubs, no separate Azure OpenAI resource.

@description('Foundry account name; also used as the custom subdomain.')
param accountName string

@description('Foundry project name.')
param projectName string

param location string
param tags object

@description('Disable API-key auth; Entra ID only.')
param disableLocalAuth bool = true

resource account 'Microsoft.CognitiveServices/accounts@2025-06-01' = {
  name: accountName
  location: location
  tags: tags
  kind: 'AIServices'
  sku: {
    name: 'S0'
  }
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    customSubDomainName: accountName
    allowProjectManagement: true
    publicNetworkAccess: 'Enabled'
    disableLocalAuth: disableLocalAuth
    networkAcls: {
      defaultAction: 'Allow'
    }
  }
}

resource project 'Microsoft.CognitiveServices/accounts/projects@2025-06-01' = {
  parent: account
  name: projectName
  location: location
  tags: tags
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    displayName: 'Contoso Oil Document Intelligence'
    description: 'Workshop project: Simple RAG vs Metadata Miner vs Smart RAG.'
  }
}

output accountId string = account.id
output accountName string = account.name
output accountPrincipalId string = account.identity.principalId
output projectId string = project.id
output projectName string = project.name
output projectPrincipalId string = project.identity.principalId
output accountEndpoint string = 'https://${accountName}.services.ai.azure.com'
output openAiEndpoint string = 'https://${accountName}.openai.azure.com'
output projectEndpoint string = 'https://${accountName}.services.ai.azure.com/api/projects/${projectName}'
