// Clean-room facilitator deployment: everything new, shared by the cohort (Topology B).
// az deployment group create -g rg-contoso-oil-<event> -f infra/main.bicep -p infra/parameters/facilitator-new.bicepparam
using '../main.bicep'

param location = 'swedencentral'
param prefix = 'contosooil'
param eventId = 'dev'

// 40 participants x 5 Search objects = 200 -> standard2 (S2) or split cohorts over
// several services. Run `uv run contoso capacity --participants 40` first.
param searchSku = 'basic'

// Facilitator (and, for Topology B, a participants Entra group). Fill in object ids.
param participants = [
  // { objectId: '<facilitator-object-id>', principalType: 'User' }
  // { objectId: '<participants-group-object-id>', principalType: 'Group' }
]

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
