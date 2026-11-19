// Topology A: one participant owns an isolated sandbox (own subscription/resource group).
// Same Bicep as the facilitator; smallest SKUs and capacities.
using '../main.bicep'

param location = 'swedencentral'
param prefix = 'fomohack'
param eventId = 'participant'
param searchSku = 'basic'

param chatModel = {
  name: 'gpt-5-mini'
  format: 'OpenAI'
  model: 'gpt-5-mini'
  version: '2025-08-07'
  sku: 'GlobalStandard'
  capacity: 20
}

param embeddingModel = {
  name: 'text-embedding-3-small'
  format: 'OpenAI'
  model: 'text-embedding-3-small'
  version: '1'
  sku: 'GlobalStandard'
  capacity: 30
}

param decisionModel = {
  name: 'decision-1-1'
  format: 'Microsoft'
  model: 'Microsoft-Decision-1.1'
  version: '1'
  sku: 'GlobalStandard'
  capacity: 10
}

param participants = [
  // { objectId: '<your-object-id>', principalType: 'User' }
]

// You own the sandbox; you can grant your agent identity yourself.
param allowParticipantAgentGrants = false

// Storage network mode (docs/troubleshooting.md#hosted-networking):
//   public    = Blob public endpoint, Entra ID only (no shared key, no anonymous). Required for the
//               hosted Metadata Miner: hosted agents reach storage over public egress.
//   perimeter = Network Security Perimeter; Search and local CLI runs work, the hosted miner gets 403.
param storageNetworkMode = 'public'

// Only if a governance Modify policy forces publicNetworkAccess=Disabled and documents an exclusion
// tag (MCAPS sandboxes: SecurityControl=Ignore). Facilitator decision; leave empty otherwise.
param storagePolicyExemptionTags = {
  // SecurityControl: 'Ignore'
}

// perimeter mode only: public egress CIDRs of machines that run the CLI (curl https://api.ipify.org).
param allowedIpPrefixes = [
  // '203.0.113.0/24'
]
