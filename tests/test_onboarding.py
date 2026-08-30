from holdmydata import identity


def test_identity_onboarding_hides_values_and_saves_counts(monkeypatch, tmp_path, capsys):
    secret_values = [
        "Example Person, Example",
        "private@example.com",
        "9999999999",
        "Example Street",
        "",
        "EXAMPLE-ID",
    ]
    answers = iter(secret_values)
    identity_path = tmp_path / "identity.yaml"
    monkeypatch.setattr(identity, "IDENTITY_PATH", identity_path)
    monkeypatch.setattr(identity.getpass, "getpass", lambda _: next(answers))

    saved = identity.run_onboarding()
    output = capsys.readouterr().out

    assert saved["names"] == ["Example Person", "Example"]
    assert saved["addresses"] == ["Example Street"]
    assert identity_path.stat().st_mode & 0o777 == 0o600
    assert all(value not in output for value in secret_values if value)
