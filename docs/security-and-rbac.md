# Security and RBAC

## 1. Principles

1. **Entra ID only.** No keys, SAS or connection strings anywhere: Storage `allowSharedKeyAccess=false`,
   Search `disableLocalAuth=true`, Foundry `disableLocalAuth=true`. `.env` is never needed.
2. **Managed identities for service-to-service.** Search indexers, the Foundry project connection
   and the hosted agent each use their own identity.
3. **Least privilege by scope.** Roles are granted at the narrowest scope Azure supports for that
   data plane: container for the hosted agent, account/service elsewhere.
4. **ABAC instead of Owner.** Participants may create role assignments, but an ABAC condition
   restricts *which* role (one GUID) to *which* principal type (`ServicePrincipal`).
5. **Plan before change.** Every mutating command prints a plan with tenant, subscription, resource
   group and resources; `--apply` and a typed confirmation for destructive changes are required.

## 2. Role matrix

| # | Principal | Role (GUID prefix) | Scope | Why | Created by |
|---|---|---|---|---|---|
| 1 | Search service MI | Storage Blob Data Reader (`2a2b9908`) | Storage account | indexers read blobs + custom metadata | Bicep |
| 2 | Search service MI | Cognitive Services OpenAI User (`5e0bd9bd`) | Foundry account | embedding skill + query vectorizer | Bicep |
| 3 | Foundry project MI | Foundry User (`53ca6127`) | Foundry account | project-level model use | Bicep |
| 4 | Foundry account + project MI | Search Index Data Reader (`1407120a`) | Search service | agents' Azure AI Search tool queries | Bicep |
| 5 | Foundry account + project MI | Search Service Contributor (`7ca78c08`) | Search service | tool reads index metadata | Bicep |
| 6 | Participant | Storage Blob Data Contributor (`ba92f5b4`) | Storage account | upload corpus, reset metadata | Bicep |
| 7 | Participant | Search Service Contributor + Search Index Data Contributor | Search service | create indexes/indexers, query | Bicep |
| 8 | Participant | Foundry User (`53ca6127`) | Foundry account | Decision-1 data plane (`try_classifier`, local mode) | Bicep |
| 9 | Participant | Foundry Project Manager (`eadc314b`) | Foundry project | create agent versions | Bicep |
| 10 | Participant | RBAC Administrator (`f58310d9`) **+ ABAC** | Storage account | grant row 12 only | Bicep |
| 11 | Participant | RBAC Administrator (`f58310d9`) **+ ABAC** | Foundry account | grant row 13 only | Bicep |
| 12 | Hosted miner identity | Storage Blob Data Contributor | **Container** `<name>-inbox` | list/read blobs, write metadata | CLI (`miner deploy`) |
| 13 | Hosted miner identity | Foundry User | Foundry account | call Decision-1 | CLI (`miner deploy`) |

Rows 12-13 cannot be Bicep: the agent identity exists only after the agent version is created.

## 3. ABAC condition (rows 10-11)

```
(!(ActionMatches{'Microsoft.Authorization/roleAssignments/write'}))
  OR ( @Request[...:RoleDefinitionId] ForAnyOfAnyValues:GuidEquals {<one role>}
       AND @Request[...:PrincipalType] StringEqualsIgnoreCase 'ServicePrincipal' )
-- same shape for /delete using @Resource[...]
```

Effect: a participant cannot grant themselves or another user anything, cannot grant Owner/
Contributor, and cannot touch other roles. Verified live: granting Foundry User to a service
principal succeeds; any other role is denied.

## 4. Isolation in the shared topology (B)

Shared topology isolates by **namespace + convention**, not hard tenancy:

| Resource | Boundary | Residual risk |
|---|---|---|
| Blob | each hosted agent gets its own **container**-scoped grant | participants (row 6) can write other containers |
| Search | names prefixed `<hackername>-` | Search RBAC is service-level; participants can see others' indexes |
| Foundry | agent names prefixed | Project Manager can see all project agents |

Mitigation: cleanup and reset only touch the **exact names derived from the participant's
hackername** (`naming.py`); each plan lists those objects before anything is deleted. For hard
isolation use topology A (one sandbox per participant).

## 5. Data and prompt safety

- Corpus is fully synthetic (`corpus/generator`, deterministic, seed in `manifest.json`).
- Document text sent to Decision-1 is truncated (`max_chars`, default 6000) and framed as data;
  the instructions say to ignore instructions inside documents.
- Logs (`.workshop/runs/<name>/*.jsonl`) redact bearer tokens, JWTs, SAS signatures, keys/passwords
  and any field named like `token`, `secret`, `credential` or `authorization`.
- The shareable readiness report (`preflight --output json`) omits subscription name, UPNs and
  object IDs; see [`examples/sample-readiness.json`](../examples/sample-readiness.json).

## 6. Networking

Storage defaults to **public endpoint + Entra-only** (`storageNetworkMode='public'`). Network-security
perimeter mode blocks the hosted agent (403); see
[troubleshooting](troubleshooting.md#hosted-networking). Private networking for hosted agents is
out of scope for a 60-minute workshop.
