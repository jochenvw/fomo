from __future__ import annotations

import io

import pytest

from contoso_workshop import plan as plan_mod
from contoso_workshop.errors import ApprovalDeclined, WorkshopError
from contoso_workshop.plan import Plan, confirm, mode_from_flags


def _plan(*states, destructive=False):
    p = Plan("provision", "cosmic-wombat", "sub", "rg", destructive=destructive)
    for i, state in enumerate(states):
        p.add("create", f"thing-{i}", state=state)
    return p


class FakeStdin(io.StringIO):
    def __init__(self, text: str, tty: bool = True) -> None:
        super().__init__(text)
        self._tty = tty

    def isatty(self) -> bool:
        return self._tty


def test_writes_exclude_skips():
    p = _plan("pending", "skip", "exists")
    assert [a.target for a in p.writes] == ["thing-0", "thing-2"]
    assert "(skip)" in p.render()


def test_unknown_verb_rejected():
    with pytest.raises(ValueError):
        _plan().add("explode", "x")


@pytest.mark.parametrize(
    ("flags", "mode"),
    [
        ((False, False, False, False), "plan"),
        ((True, False, False, False), "check"),
        ((False, True, False, False), "plan"),
        ((False, False, False, True), "plan"),
        ((False, False, True, False), "apply"),
        ((False, True, False, True), "plan"),
    ],
)
def test_mode_from_flags(flags, mode):
    assert mode_from_flags(*flags) == mode


def test_mode_from_flags_conflict():
    with pytest.raises(WorkshopError):
        mode_from_flags(False, True, True, False)


def test_confirm_yes_and_nothing_to_do(monkeypatch):
    monkeypatch.setattr(plan_mod.sys, "stdin", FakeStdin("", tty=False))
    confirm(_plan("pending"), yes=True)
    confirm(_plan("skip"), yes=False)  # no writes -> no question


def test_confirm_non_interactive_requires_yes(monkeypatch):
    monkeypatch.setattr(plan_mod.sys, "stdin", FakeStdin("yes\n", tty=False))
    with pytest.raises(ApprovalDeclined):
        confirm(_plan("pending"), yes=False)


@pytest.mark.parametrize(("answer", "ok"), [("yes\n", True), ("y\n", False), ("no\n", False), ("\n", False)])
def test_confirm_interactive(monkeypatch, answer, ok):
    monkeypatch.setattr(plan_mod.sys, "stdin", FakeStdin(answer))
    if ok:
        confirm(_plan("pending"), yes=False)
    else:
        with pytest.raises(ApprovalDeclined):
            confirm(_plan("pending"), yes=False)


@pytest.mark.parametrize(("answer", "ok"), [("DELETE\n", True), ("yes\n", False), ("delete\n", False)])
def test_destructive_requires_typed_delete(monkeypatch, capsys, answer, ok):
    monkeypatch.setattr(plan_mod.sys, "stdin", FakeStdin(answer))
    p = _plan("pending", destructive=True)
    if ok:
        confirm(p, yes=False)
    else:
        with pytest.raises(ApprovalDeclined):
            confirm(p, yes=False)
    assert "Type 'DELETE'" in capsys.readouterr().err
