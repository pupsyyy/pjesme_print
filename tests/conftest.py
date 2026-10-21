"""Zajedničko za testove."""

import pytest


@pytest.fixture(autouse=True)
def _no_auto_backup(monkeypatch):
    # dnevni backup u pozadini ne treba u testovima (test_features ga provjerava izravno)
    monkeypatch.setenv("BACKUP_DISABLE", "1")
