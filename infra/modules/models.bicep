// The three model deployments the workshop needs: chat, embeddings, Microsoft-Decision-1.1
// (plus an optional facilitator evaluation judge).
// Deployments on one account must be created serially (@batchSize(1)).

param accountName string

@description('Array of {name, format, model, version, sku, capacity}.')
param deployments array

resource account 'Microsoft.CognitiveServices/accounts@2025-06-01' existing = {
  name: accountName
}

@batchSize(1)
resource deployment 'Microsoft.CognitiveServices/accounts/deployments@2025-06-01' = [
  for d in deployments: {
    parent: account
    name: d.name
    sku: {
      name: d.sku
      capacity: d.capacity
    }
    properties: {
      model: {
        format: d.format
        name: d.model
        version: d.version
      }
      versionUpgradeOption: 'NoAutoUpgrade'
    }
  }
]

output deploymentNames array = [for (d, i) in deployments: deployment[i].name]
