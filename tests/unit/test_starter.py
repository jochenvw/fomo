from __future__ import annotations

import pytest

from contoso_workshop.decision.config import load_classifier_config
from contoso_workshop.errors import ConfigError
from contoso_workshop.provision.starter import write_starter


@pytest.fixture
def in_tmp(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    return tmp_path


def test_write_starter_creates_files(in_tmp, names):
    folder, created = write_starter(names)
    assert created
    assert folder == in_tmp.joinpath(".workshop", "runs", "cosmic-wombat", "miner").relative_to(in_tmp)
    classifier = (folder / "classifier.yaml").read_text(encoding="utf-8")
    assert "TODO" in classifier
    assert "version: cosmic-wombat-v1" in classifier
    assert (folder / "try_classifier.py").exists()
    assert "cosmic-wombat-metadata-miner" in (folder / "README.md").read_text(encoding="utf-8")
    compile((folder / "try_classifier.py").read_text(encoding="utf-8"), "try_classifier.py", "exec")


def test_starter_classifier_rejected_until_todos_are_done(in_tmp, names):
    folder, _ = write_starter(names)
    path = folder / "classifier.yaml"
    with pytest.raises(ConfigError, match="TODO"):
        load_classifier_config(path)
    text = path.read_text(encoding="utf-8")
    text = text.replace("TODO: describe what makes", "Describes what makes").replace(
        "TODO: describe slide decks", "Slide decks")
    path.write_text(text, encoding="utf-8")
    assert load_classifier_config(path).version == "cosmic-wombat-v1"


def test_rerun_preserves_edits_and_restores_missing_helpers(in_tmp, names):
    folder, _ = write_starter(names)
    classifier = folder / "classifier.yaml"
    classifier.write_text("# my edits\n", encoding="utf-8")
    (folder / "try_classifier.py").unlink()
    _, created = write_starter(names)
    assert not created
    assert classifier.read_text(encoding="utf-8") == "# my edits\n"
    assert (folder / "try_classifier.py").exists()


def test_force_overwrites(in_tmp, names):
    folder, _ = write_starter(names)
    (folder / "classifier.yaml").write_text("# my edits\n", encoding="utf-8")
    _, created = write_starter(names, force=True)
    assert created
    assert "TODO" in (folder / "classifier.yaml").read_text(encoding="utf-8")
