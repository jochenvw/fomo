from __future__ import annotations

from pathlib import Path

import pytest

from contoso_workshop.preflight import BLOCKED, INFO, PASS, REPO_ROOT, WARNING, Report, _skill_ok, effective, scrub

PERMS = [
    {"actions": ["*"], "notActions": ["Microsoft.Authorization/*/Write", "Microsoft.Authorization/*/Delete"]},
    {"actions": ["Microsoft.Storage/*/read"], "notActions": [],
     "dataActions": ["Microsoft.Storage/storageAccounts/blobServices/containers/blobs/*"],
     "notDataActions": ["Microsoft.Storage/storageAccounts/blobServices/containers/blobs/delete"]},
]


@pytest.mark.parametrize(
    ("action", "data", "expected"),
    [
        ("Microsoft.Search/searchServices/write", False, True),
        ("Microsoft.Authorization/roleAssignments/write", False, False),  # Contributor-like
        ("microsoft.storage/storageAccounts/read", False, True),  # case-insensitive
        ("Microsoft.Storage/storageAccounts/blobServices/containers/blobs/read", True, True),
        ("Microsoft.Storage/storageAccounts/blobServices/containers/blobs/delete", True, False),
        ("Microsoft.Search/searchServices/indexes/documents/read", True, False),
    ],
)
def test_effective_permissions(action, data, expected):
    assert effective(PERMS, action, data=data) is expected


def test_not_actions_only_exclude_within_same_entry():
    perms = [{"actions": ["Microsoft.Storage/*/read"], "notActions": []},
             {"actions": ["*"], "notActions": ["Microsoft.Storage/*"]}]
    assert effective(perms, "Microsoft.Storage/storageAccounts/read")
    assert not effective(perms, "Microsoft.Storage/storageAccounts/write")


def test_scrub_masks_ids_upns_and_sensitive_strings():
    sub = "0b1c2d3e-4f50-6172-8394-a5b6c7d8e9f0"
    obj = {"scope": f"/subscriptions/{sub}/resourceGroups/rg", "items": [
        "signed in as jane.doe@contoso.onmicrosoft.com", {"name": "Contoso Prod Subscription (EU)"}],
        "token": "abc", "count": 3}
    out = scrub(obj, {"Contoso Prod Subscription", "Contoso Prod"})
    assert out["scope"] == "/subscriptions/<id>/resourceGroups/rg"
    assert out["items"][0] == "signed in as <user>"
    assert out["items"][1]["name"] == "<redacted> (EU)"
    assert out["token"] == "***"
    assert out["count"] == 3
    assert sub not in str(out)


def _report(*statuses):
    rep = Report("now", 40)
    for i, (status, required) in enumerate(statuses):
        rep.add(f"c{i}", "cat", "t", status, required=required)
    return rep


def test_report_verdict():
    assert _report((PASS, True), (INFO, False)).verdict == PASS
    assert _report((PASS, True), (WARNING, True)).verdict == WARNING
    assert _report((PASS, True), (BLOCKED, False)).verdict == WARNING  # optional blockers only warn
    assert _report((WARNING, True), (BLOCKED, True)).verdict == BLOCKED
    assert _report((PASS, True), (PASS, True)).counts() == {PASS: 2}


def test_repo_skills_have_valid_front_matter():
    skills = sorted(REPO_ROOT.glob(".github/skills/*/SKILL.md"))
    assert len(skills) >= 3
    assert all(_skill_ok(s) for s in skills), [s.parent.name for s in skills if not _skill_ok(s)]


@pytest.mark.parametrize(
    "text",
    [
        "no front matter\n",
        "---\nname: other-name\ndescription: x\n---\nbody\n",
        "---\nname: my-skill\n---\nbody\n",
    ],
)
def test_bad_skill_front_matter(tmp_path: Path, text):
    path = tmp_path / "my-skill" / "SKILL.md"
    path.parent.mkdir()
    path.write_text(text, encoding="utf-8")
    assert not _skill_ok(path)


def test_good_skill_with_crlf(tmp_path: Path):
    path = tmp_path / "my-skill" / "SKILL.md"
    path.parent.mkdir()
    path.write_bytes(b"---\r\nname: my-skill\r\ndescription: does things\r\n---\r\nbody\r\n")
    assert _skill_ok(path)
