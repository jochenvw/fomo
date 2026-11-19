"""Index, skillset, data source and indexer definitions (Search REST JSON).

Fairness invariant: raw and smart share ONE chunking + embedding definition
(`ChunkingSettings`, `_skills`, `_vector_search`). The only differences are the three
classification fields in the smart index and their projection mappings. Tests assert this.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..config import Environment
from ..naming import ParticipantNames

API_VERSION = "2025-09-01"
VECTOR_PROFILE = "vec-profile"
VECTORIZER = "aoai-vectorizer"
SCORING_PROFILE = "boost-contracts"

CLASSIFICATION_FIELDS = ("document_type", "classification_confidence", "classification_version")


@dataclass(frozen=True)
class ChunkingSettings:
    max_page_length: int = 2000
    overlap: int = 200
    extensions: str = ".pdf,.pptx"


CHUNKING = ChunkingSettings()


def _base_fields(dims: int) -> list[dict[str, Any]]:
    return [
        {"name": "chunk_id", "type": "Edm.String", "key": True, "searchable": True, "analyzer": "keyword",
         "retrievable": True, "filterable": False, "sortable": True},
        {"name": "parent_id", "type": "Edm.String", "searchable": False, "filterable": True,
         "retrievable": True},
        {"name": "doc_id", "type": "Edm.String", "searchable": False, "filterable": True, "facetable": True,
         "retrievable": True},
        {"name": "title", "type": "Edm.String", "searchable": True, "retrievable": True},
        {"name": "content", "type": "Edm.String", "searchable": True, "retrievable": True},
        {"name": "url", "type": "Edm.String", "searchable": False, "retrievable": True, "filterable": True},
        {"name": "content_vector", "type": "Collection(Edm.Single)", "searchable": True,
         "retrievable": False, "stored": False, "dimensions": dims, "vectorSearchProfile": VECTOR_PROFILE},
    ]


def _classification_fields() -> list[dict[str, Any]]:
    return [
        {"name": "document_type", "type": "Edm.String", "searchable": False, "filterable": True,
         "facetable": True, "retrievable": True},
        {"name": "classification_confidence", "type": "Edm.Double", "searchable": False, "filterable": True,
         "sortable": True, "retrievable": True},
        {"name": "classification_version", "type": "Edm.String", "searchable": False, "filterable": True,
         "retrievable": True},
    ]


def _vector_search(env: Environment) -> dict[str, Any]:
    return {
        "algorithms": [{"name": "hnsw", "kind": "hnsw", "hnswParameters": {"metric": "cosine"}}],
        "profiles": [{"name": VECTOR_PROFILE, "algorithm": "hnsw", "vectorizer": VECTORIZER}],
        "vectorizers": [
            {
                "name": VECTORIZER,
                "kind": "azureOpenAI",
                "azureOpenAIParameters": {
                    "resourceUri": env.resolved_openai_endpoint(),
                    "deploymentId": env.embedding_deployment,
                    "modelName": env.embedding_model,
                },
            }
        ],
    }


def index_definition(env: Environment, name: str, *, smart: bool) -> dict[str, Any]:
    fields = _base_fields(env.embedding_dimensions)
    body: dict[str, Any] = {"name": name, "fields": fields, "vectorSearch": _vector_search(env)}
    if smart:
        fields.extend(_classification_fields())
        # Optional second experiment (boosting). Only used by direct evaluation queries that pass
        # scoringProfile + scoringParameters; the Foundry tool does not.
        body["scoringProfiles"] = [
            {
                "name": SCORING_PROFILE,
                "functionAggregation": "sum",
                "functions": [
                    {"type": "tag", "fieldName": "document_type", "boost": 3,
                     "interpolation": "constant", "tag": {"tagsParameter": "types"}}
                ],
            }
        ]
    return body


def datasource_definition(env: Environment, names: ParticipantNames) -> dict[str, Any]:
    return {
        "name": names.datasource,
        "type": "azureblob",
        # Keyless: ResourceId connection string => the Search system managed identity.
        "credentials": {"connectionString": f"ResourceId={env.resolved_storage_account_id()};"},
        "container": {"name": names.container},
        "dataDeletionDetectionPolicy": {
            "@odata.type": "#Microsoft.Azure.Search.NativeBlobSoftDeleteDeletionDetectionPolicy"
        },
        "description": f"FOMO workshop inbox for {names.hackername}",
    }


def _skills(env: Environment) -> list[dict[str, Any]]:
    return [
        {
            "@odata.type": "#Microsoft.Skills.Text.SplitSkill",
            "name": "chunk",
            "context": "/document",
            "textSplitMode": "pages",
            "maximumPageLength": CHUNKING.max_page_length,
            "pageOverlapLength": CHUNKING.overlap,
            "inputs": [{"name": "text", "source": "/document/content"}],
            "outputs": [{"name": "textItems", "targetName": "pages"}],
        },
        {
            "@odata.type": "#Microsoft.Skills.Text.AzureOpenAIEmbeddingSkill",
            "name": "embed",
            "context": "/document/pages/*",
            # No apiKey/authIdentity => Search system-assigned identity (Cognitive Services OpenAI User).
            "resourceUri": env.resolved_openai_endpoint(),
            "deploymentId": env.embedding_deployment,
            "modelName": env.embedding_model,
            "dimensions": env.embedding_dimensions,
            "inputs": [{"name": "text", "source": "/document/pages/*"}],
            "outputs": [{"name": "embedding", "targetName": "content_vector"}],
        },
    ]


def _classification_defaults() -> list[dict[str, Any]]:
    """Unclassified blobs lack the metadata keys; projections warn on missing paths.
    ConditionalSkill is the documented way to supply defaults."""
    defaults = {"document_type": "'unclassified'", "classification_confidence": "0",
                "classification_version": "'none'"}
    return [
        {
            "@odata.type": "#Microsoft.Skills.Util.ConditionalSkill",
            "name": f"default-{key}",
            "context": "/document",
            "inputs": [
                {"name": "condition", "source": f"= $(/document/{key}) == null"},
                {"name": "whenTrue", "source": f"= {default}"},
                {"name": "whenFalse", "source": f"= $(/document/{key})"},
            ],
            "outputs": [{"name": "output", "targetName": f"{key}_v"}],
        }
        for key, default in defaults.items()
    ]


def skillset_definition(env: Environment, name: str, target_index: str, *, smart: bool) -> dict[str, Any]:
    mappings = [
        {"name": "content", "source": "/document/pages/*"},
        {"name": "content_vector", "source": "/document/pages/*/content_vector"},
        {"name": "title", "source": "/document/title"},
        {"name": "url", "source": "/document/metadata_storage_path"},
        {"name": "doc_id", "source": "/document/doc_id"},
    ]
    skills = _skills(env)
    if smart:
        # Blob custom metadata surfaces at /document/<key>. Only the smart index maps it.
        skills += _classification_defaults()
        mappings += [{"name": f, "source": f"/document/{f}_v"} for f in CLASSIFICATION_FIELDS]
    return {
        "name": name,
        "description": "FOMO chunk + embed (" + ("smart" if smart else "raw") + ")",
        "skills": skills,
        "indexProjections": {
            "selectors": [
                {
                    "targetIndexName": target_index,
                    "parentKeyFieldName": "parent_id",
                    "sourceContext": "/document/pages/*",
                    "mappings": mappings,
                }
            ],
            "parameters": {"projectionMode": "skipIndexingParentDocuments"},
        },
    }


def indexer_definition(name: str, datasource: str, skillset: str, target_index: str, *,
                       disabled: bool = False) -> dict[str, Any]:
    return {
        "name": name,
        "dataSourceName": datasource,
        "skillsetName": skillset,
        "targetIndexName": target_index,
        # Provision creates the smart indexer disabled ("pending"): no embedding spend and no
        # unclassified smart index until cheatcode / the participant enables it after mining.
        "disabled": disabled,
        "parameters": {
            "batchSize": 10,
            "maxFailedItems": 0,
            "configuration": {
                "dataToExtract": "contentAndMetadata",
                "parsingMode": "default",
                "indexedFileNameExtensions": CHUNKING.extensions,
                "failOnUnsupportedContentType": False,
            },
        },
    }


def participant_definitions(env: Environment, names: ParticipantNames, *, smart: bool,
                            pending: bool = False) -> dict[str, Any]:
    if smart:
        idx, sks, ixr = names.smart_index, names.smart_skillset, names.smart_indexer
    else:
        idx, sks, ixr = names.raw_index, names.raw_skillset, names.raw_indexer
    return {
        "index": index_definition(env, idx, smart=smart),
        "skillset": skillset_definition(env, sks, idx, smart=smart),
        "indexer": indexer_definition(ixr, names.datasource, sks, idx, disabled=pending),
    }
