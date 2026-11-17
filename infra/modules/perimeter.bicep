// Network Security Perimeter (NSP) around the storage account (storageNetworkMode = 'perimeter').
// Why: tenant governance (e.g. SFI "Secure PaaS Resources") may force Storage publicNetworkAccess
// to Disabled unless the account is SecuredByPerimeter. An Enforced NSP association lets Search
// indexers (managed identity in this subscription) and listed participant egress IPs reach Blob.
// Limitation (verified): Foundry hosted agents are NOT admitted by the subscription rule, and adding the
// Foundry account to the perimeter (Learning mode) does not help either. Hosted miner -> use 'public' mode,
// or a Foundry account created with agent VNet injection plus a Blob private endpoint.

param perimeterName string
param location string
param tags object
param storageAccountId string
@description('Subscriptions whose managed identities may reach storage (Search indexers).')
param allowedSubscriptionIds array = [subscription().id]
@description('Public egress CIDRs of facilitator and participant machines (venue NAT, VPN). Empty = none.')
param allowedIpPrefixes array = []

resource nsp 'Microsoft.Network/networkSecurityPerimeters@2024-07-01' = {
  name: perimeterName
  location: location
  tags: tags
}

resource profile 'Microsoft.Network/networkSecurityPerimeters/profiles@2024-07-01' = {
  parent: nsp
  name: 'workshop'
}

resource subsRule 'Microsoft.Network/networkSecurityPerimeters/profiles/accessRules@2024-07-01' = {
  parent: profile
  name: 'allow-subscriptions'
  properties: {
    direction: 'Inbound'
    subscriptions: [for s in allowedSubscriptionIds: { id: s }]
  }
}

resource ipRule 'Microsoft.Network/networkSecurityPerimeters/profiles/accessRules@2024-07-01' = if (!empty(allowedIpPrefixes)) {
  parent: profile
  name: 'allow-participant-ips'
  properties: {
    direction: 'Inbound'
    addressPrefixes: allowedIpPrefixes
  }
}

resource storageAssoc 'Microsoft.Network/networkSecurityPerimeters/resourceAssociations@2024-07-01' = {
  parent: nsp
  name: 'storage'
  properties: {
    privateLinkResource: { id: storageAccountId }
    profile: { id: profile.id }
    accessMode: 'Enforced'
  }
  dependsOn: [subsRule, ipRule]
}

output perimeterId string = nsp.id
