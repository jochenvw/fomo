// Reuse customer-provisioned resources. Only the Search connection, RBAC and
// (optionally) missing model deployments are created.
// az deployment group create -g <foundry-rg> -f infra/reuse-existing.bicep -p infra/parameters/reuse-existing.bicepparam
using '../reuse-existing.bicep'

param foundryAccountName = '<existing-foundry-account>'
param foundryProjectName = '<existing-project>'

param storageAccountName = '<existing-storage-account>'
param storageResourceGroup = '<storage-rg>'

param searchServiceName = '<existing-search-service>'
param searchResourceGroup = '<search-rg>'

// Leave empty if gpt-5-mini, text-embedding-3-small and Microsoft-Decision-1.1 already exist.
param createModelDeployments = []

param participants = []

// Set false if you lack Microsoft.Authorization/roleAssignments/write; preflight prints the
// exact grants for an admin instead.
param assignRoles = true
