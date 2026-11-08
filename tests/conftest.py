"""Signed temporary grants for pre-license regression tests; no production bypass."""
import base64
import time
import uuid

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from src.everai.license_manager import LicenseManager, PRODUCT, canonical


@pytest.fixture
def funded_license(tmp_path, monkeypatch):
    from src.everai import engine, gui
    private = Ed25519PrivateKey.generate()
    key_id = "1" * 32
    keys = {key_id: base64.b64encode(private.public_key().public_bytes_raw()).decode()}
    machine = "EV1-" + "A" * 64
    manager = LicenseManager(tmp_path / "test-wallet", keys=keys, machine=machine)
    payload = dict(schema_version=1, product=PRODUCT, key_id=key_id, license_id=str(uuid.uuid4()),
                   machine_id=machine, char_allowance=10**8, issued_at=int(time.time()))
    manager.import_license(canonical(dict(payload=payload, signature=base64.b64encode(private.sign(canonical(payload))).decode())))
    monkeypatch.setattr(engine, "LicenseManager", lambda: manager)
    monkeypatch.setattr(gui, "LicenseManager", lambda root: manager)
    return manager
