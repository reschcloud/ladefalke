"""Betrieb: Versionsanzeige aus Git-Tags, Healthcheck /gesund."""
from app import version as v

C1 = "a" * 40
C2 = "b" * 40
TAG_OBJEKT = "c" * 40


def _git(tmp_path, kopf: str, dateien: dict[str, str]):
    git = tmp_path / ".git"
    for name, inhalt in {"HEAD": kopf, **dateien}.items():
        (git / name).parent.mkdir(parents=True, exist_ok=True)
        (git / name).write_text(inhalt)
    return git


def test_commit_und_tag_lose_refs(tmp_path):
    git = _git(tmp_path, "ref: refs/heads/main\n", {"refs/heads/main": C1 + "\n", "refs/tags/v1.0.0": C1 + "\n",
                                                  "refs/tags/v1.2.0": C1 + "\n", "refs/tags/v1.10.0": C2 + "\n",
                                                  "refs/tags/test": C1 + "\n"})
    assert v._commit(git) == C1
    assert v._tag(git, C1) == "v1.2.0"  # höchster Versions-Tag auf dem Commit; „test“ ist keine Version
    assert v._tag(git, C2) == "v1.10.0"  # numerisch, nicht alphabetisch
    assert v._tag(git, "d" * 40) is None


def test_tag_aus_packed_refs_auch_annotiert(tmp_path):
    """Losgelöster HEAD (git checkout v1.1.0); annotierter Tag: packed-refs nennt den Commit in der „^“-Zeile."""
    git = _git(tmp_path, C2 + "\n", {"packed-refs": "# pack-refs with: peeled fully-peeled sorted\n"
                                                    f"{C1} refs/heads/main\n{TAG_OBJEKT} refs/tags/v1.1.0\n^{C2}\n"})
    assert v._commit(git) == C2
    assert v._tag(git, C2) == "v1.1.0"
    assert v._commit(_git(tmp_path / "x", "ref: refs/heads/main\n", {"packed-refs": f"{C1} refs/heads/main\n"})) == C1


def test_gesund(client):
    """Ohne Anmeldung erreichbar, nur der Status."""
    antwort = client.get("/gesund")
    assert antwort.status_code == 200 and antwort.json() == {"status": "ok"}


def test_version_aus_datei(tmp_path, monkeypatch):
    """Image der Release-Pipeline: VERSION-Datei geht vor dem Git-Stand (im Image ist kein .git)."""
    _git(tmp_path, "ref: refs/heads/main\n", {"refs/heads/main": C1 + "\n"})
    monkeypatch.setattr(v, "WURZEL", tmp_path)
    v.version.cache_clear()
    try:
        assert v.version().startswith("a" * 7)
        (tmp_path / "VERSION").write_text("v1.0.1\n")
        (tmp_path / "BUILD_DATUM").write_text("2026-11-18\n")
        v.version.cache_clear()
        assert v.version() == "v1.0.1 · 18.11.2026"
    finally:
        v.version.cache_clear()
