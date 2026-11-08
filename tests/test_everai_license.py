"""Offline grants, crash consistency, wallet concurrency and customer packaging boundaries."""
import base64
import hashlib
import json
import sqlite3
import subprocess
import sys
import threading
import time
import uuid
import zipfile
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from src.everai.license_manager import (LicenseManager, LicenseError, PRODUCT, canonical,
                                      verify_license, MAX_LICENSE_BYTES, encode_license_key,
                                      decode_license_key, MAX_KEY_CHARS)
from src.everai.engine import EverAITTSEngine
from src.everai.client import EverAIConnectionError
from src.everai import utils
from src.everai.admin_key import admin_tool

MACHINE = "EV1-" + "B"*64
PASSWORD = "test-only-recovery-password-12345"


@pytest.fixture
def timed_issuer(monkeypatch):
    from src.everai import license_manager as licensing
    private = Ed25519PrivateKey.generate()
    key_id = hashlib.sha256(private.public_key().public_bytes_raw()).hexdigest()[:32]
    keys = {key_id:base64.b64encode(private.public_key().public_bytes_raw()).decode()}
    clock = SimpleNamespace(now=int(datetime(2030,1,15,12,tzinfo=licensing.LICENSE_TIMEZONE).timestamp()))
    monkeypatch.setattr(licensing,"time",SimpleNamespace(time=lambda:clock.now))
    def issue(amount=100, day="15/01/2030", **overrides):
        payload = dict(schema_version=2,product=PRODUCT,key_id=key_id,license_id=str(uuid.uuid4()),
                       machine_id=MACHINE,char_allowance=amount,issued_at=clock.now,
                       expires_at=licensing.expiry_from_date(day))
        payload.update(overrides)
        return canonical(dict(payload=payload,signature=base64.b64encode(private.sign(canonical(payload))).decode()))
    return keys,issue,clock


def test_vietnam_date_is_inclusive_and_independent_of_host_timezone():
    from src.everai.license_manager import expiry_from_date, expiry_label, LICENSE_TIMEZONE
    deadline = expiry_from_date("15/01/2030")
    assert deadline == expiry_from_date("2030-01-15")
    assert datetime.fromtimestamp(deadline,LICENSE_TIMEZONE).isoformat() == "2030-01-16T00:00:00+07:00"
    assert expiry_label(deadline) == "15/01/2030"


@pytest.mark.parametrize("value",["31/02/2030","00/01/2030","15/13/2030","not a date","", "31/12/9999"])
def test_invalid_expiry_date_is_rejected(value):
    from src.everai.license_manager import expiry_from_date
    with pytest.raises(LicenseError):
        expiry_from_date(value)


@pytest.mark.parametrize("value",[True,0,-1,1.5,"2030-01-15",10**30])
def test_invalid_signed_expiry_is_rejected(tmp_path,timed_issuer,value):
    keys,issue,_ = timed_issuer
    manager = LicenseManager(tmp_path,keys=keys,machine=MACHINE)
    with pytest.raises(LicenseError):
        manager.import_license(issue(expires_at=value))


def test_expiry_boundary_preserves_balance_and_survives_restart(tmp_path,timed_issuer):
    from src.everai.license_manager import expiry_from_date
    keys,issue,clock = timed_issuer
    manager = LicenseManager(tmp_path,keys=keys,machine=MACHINE)
    data = issue()
    manager.import_license(data)
    deadline = expiry_from_date("15/01/2030")
    clock.now = deadline-1
    manager.ensure_active()
    assert manager.balance()["available"] == 100
    clock.now = deadline
    assert manager.balance(include_expiry=True) == dict(total=100,used=0,held=0,available=0,
                                                        remaining=100,expired=True,expires_at=deadline)
    with pytest.raises(LicenseError,match="hết hạn"):
        manager.ensure_active()
    reopened = LicenseManager(tmp_path,keys=keys,machine=MACHINE)
    assert reopened.balance()["available"] == 0
    assert not reopened.import_license(data)
    assert reopened.balance()["total"] == 100


def test_dated_renewal_restores_unused_credit_without_shortening(tmp_path,timed_issuer):
    from src.everai.license_manager import expiry_from_date
    keys,issue,clock = timed_issuer
    manager = LicenseManager(tmp_path,keys=keys,machine=MACHINE)
    manager.import_license(issue(100))
    path = tmp_path/"audio.mp3"
    with manager.job("spent","fp",["hello"],[path]):
        path.write_bytes(b"ID3hello")
        manager.commit_chunk("spent",1,path)
    clock.now = expiry_from_date("15/01/2030")
    assert manager.balance()["available"] == 0
    manager.import_license(issue(50,day="31/01/2030"))
    assert manager.balance() == dict(total=150,used=5,held=0,available=145)
    manager.import_license(issue(10,day="20/01/2030"))
    assert manager.balance(include_expiry=True)["expires_at"] == expiry_from_date("31/01/2030")


def test_legacy_credit_cannot_remove_dated_deadline(tmp_path,timed_issuer):
    from src.everai.license_manager import expiry_from_date
    keys,issue,clock = timed_issuer
    manager = LicenseManager(tmp_path,keys=keys,machine=MACHINE)
    # A trusted v1 issuer can add credit but cannot remove an existing v2 deadline.
    private = Ed25519PrivateKey.generate()
    old_id = "e"*32
    manager.keys[old_id] = base64.b64encode(private.public_key().public_bytes_raw()).decode()
    legacy_payload = dict(schema_version=1,product=PRODUCT,key_id=old_id,machine_id=MACHINE,
                          char_allowance=100,issued_at=clock.now)
    def old_grant():
        payload = dict(legacy_payload,license_id=str(uuid.uuid4()))
        return canonical(dict(payload=payload,signature=base64.b64encode(private.sign(canonical(payload))).decode()))
    manager.import_license(old_grant())
    assert manager.balance(include_expiry=True)["expires_at"] is None
    manager.import_license(issue())
    clock.now = expiry_from_date("15/01/2030")
    manager.import_license(old_grant())
    assert manager.balance()["total"] == 300
    assert manager.balance()["available"] == 0


def test_expired_grant_cannot_be_imported_and_expiry_is_signed(tmp_path,timed_issuer):
    from src.everai.license_manager import expiry_from_date
    keys,issue,clock = timed_issuer
    manager = LicenseManager(tmp_path,keys=keys,machine=MACHINE)
    data = issue()
    tampered = json.loads(data)
    tampered["payload"]["expires_at"] = expiry_from_date("31/01/2030")
    with pytest.raises(LicenseError,match="chữ ký"):
        manager.import_license(canonical(tampered))
    clock.now = expiry_from_date("15/01/2030")
    with pytest.raises(LicenseError,match="hết hạn"):
        manager.import_license(data)
    assert manager.balance()["total"] == 0


def test_expired_license_blocks_provider_even_with_unused_characters(tmp_path,timed_issuer):
    from src.everai.license_manager import expiry_from_date
    keys,issue,clock = timed_issuer
    manager = LicenseManager(tmp_path,keys=keys,machine=MACHINE)
    manager.import_license(issue())
    clock.now = expiry_from_date("15/01/2030")
    with patch("src.everai.engine.EverAIClient") as provider:
        with pytest.raises(LicenseError,match="hết hạn"):
            EverAITTSEngine(license_manager=manager).process("hello",tmp_path/"out.mp3",job_dir=tmp_path/"job")
        provider.assert_not_called()
    assert manager.balance(include_expiry=True)["remaining"] == 100


@pytest.mark.parametrize("damage", [None, "missing", "corrupt"])
def test_expired_license_allows_only_reassembly_of_saved_paid_audio(tmp_path, timed_issuer, damage):
    from src.everai.license_manager import expiry_from_date
    keys, issue, clock = timed_issuer
    manager = LicenseManager(tmp_path, keys=keys, machine=MACHINE)
    manager.import_license(issue())
    client = MagicMock()
    client.synthesize_one_chunk.return_value = b"ID3hello"
    client.get_traffic_stats.return_value = {}
    job = tmp_path / "job"
    with patch("src.everai.engine.EverAIClient", return_value=client):
        with patch("src.everai.engine.concat_mp3_files", side_effect=OSError("disk")):
            with pytest.raises(OSError):
                EverAITTSEngine(license_manager=manager).process("hello", tmp_path / "out.mp3", job_dir=job)
    assert manager.balance()["used"] == 5
    chunk = next(job.glob("*.mp3"))
    if damage == "missing":
        chunk.unlink()
    elif damage == "corrupt":
        chunk.write_bytes(b"corrupt")
    clock.now = expiry_from_date("15/01/2030")
    with patch("src.everai.engine.EverAIClient") as provider, patch("src.everai.engine.concat_mp3_files", side_effect=fake_concat) as merge:
        engine = EverAITTSEngine(license_manager=manager)
        if damage:
            with pytest.raises(LicenseError, match="hết hạn"):
                engine.process("hello", tmp_path / "new.mp3", job_dir=job)
            merge.assert_not_called()
        else:
            result = engine.process("hello", tmp_path / "new.mp3", job_dir=job)
            assert result.read_bytes() == b"ID3hello"
            merge.assert_called_once()
        provider.assert_not_called()
    assert manager.balance()["used"] == 5
    assert manager.balance()["held"] == 0


def test_deadline_between_chunks_finishes_inflight_and_stops_next_request(tmp_path,timed_issuer):
    from src.everai.license_manager import expiry_from_date
    keys,issue,clock = timed_issuer
    manager = LicenseManager(tmp_path,keys=keys,machine=MACHINE)
    manager.import_license(issue())
    client = MagicMock()
    def complete(**kwargs):
        clock.now = expiry_from_date("15/01/2030")
        return b"ID3completed-inflight"
    client.synthesize_one_chunk.side_effect = complete
    client.get_traffic_stats.return_value = {}
    with patch("src.everai.engine.EverAIClient",return_value=client),patch("src.everai.engine.split_text_into_chunks",return_value=["hello","there"]):
        with pytest.raises(LicenseError,match="hết hạn"):
            EverAITTSEngine(license_manager=manager,concurrency=1).process("text",tmp_path/"out.mp3",job_dir=tmp_path/"job")
    assert client.synthesize_one_chunk.call_count == 1
    assert manager.balance(include_expiry=True)["remaining"] == 95
    assert manager.balance()["used"] == 5 and manager.balance()["held"] == 0


def test_deadline_between_retries_does_not_send_again(tmp_path,timed_issuer):
    from src.everai.license_manager import expiry_from_date
    keys,issue,clock = timed_issuer
    manager = LicenseManager(tmp_path,keys=keys,machine=MACHINE)
    manager.import_license(issue())
    client = MagicMock()
    def fail(**kwargs):
        clock.now = expiry_from_date("15/01/2030")
        raise EverAIConnectionError("offline")
    client.synthesize_one_chunk.side_effect = fail
    client.get_traffic_stats.return_value = {}
    with patch("src.everai.engine.EverAIClient",return_value=client):
        with pytest.raises(LicenseError,match="hết hạn"):
            EverAITTSEngine(license_manager=manager,retry_delay_sec=0).process("hello",tmp_path/"out.mp3",job_dir=tmp_path/"job")
    assert client.synthesize_one_chunk.call_count == 1
    assert manager.balance()["used"] == 0 and manager.balance()["held"] == 0


def test_expiry_label_in_gui_refreshes_without_restart(tmp_path,timed_issuer,monkeypatch):
    import tkinter as tk
    from src.everai import gui
    from src.everai.license_manager import expiry_from_date
    keys,issue,clock = timed_issuer
    manager = LicenseManager(tmp_path,keys=keys,machine=MACHINE)
    manager.import_license(issue())
    monkeypatch.setattr(gui,"LicenseManager",lambda root:manager)
    root = tk.Tk()
    root.withdraw()
    app = gui.EverAIGuiApp(root,tmp_path)
    try:
        assert "15/01/2030" in app.license_panel.balance_text.get()
        clock.now = expiry_from_date("15/01/2030")
        app.license_panel.refresh(force=True)
        assert "Đã hết hạn" in app.license_panel.balance_text.get()
        assert "100 ký tự" in app.license_panel.balance_text.get()
        assert "hết hạn" in app.balance_summary.get()
    finally:
        app._destroy()


def test_admin_calendar_allows_month_year_and_specific_day():
    import tkinter as tk
    from tkinter import ttk
    root = tk.Tk()
    root.withdraw()
    variable = tk.StringVar(value="15/01/2030")
    try:
        admin_tool.choose_expiry_date(root,variable)
        popup = next(widget for widget in root.winfo_children() if isinstance(widget,tk.Toplevel))
        def descendants(widget):
            for child in widget.winfo_children():
                yield child
                yield from descendants(child)
        button = next(widget for widget in descendants(popup) if isinstance(widget,ttk.Button) and widget.cget("text")=="16")
        button.invoke()
        assert variable.get() == "16/01/2030"
    finally:
        root.destroy()


def test_admin_issues_signed_deadline_and_refuses_past_date(tmp_path):
    from src.everai.license_manager import expiry_from_date
    root,public = tmp_path/"owner",tmp_path/"public.json"
    admin_tool.create_keys(root,PASSWORD,public)
    license_file = admin_tool.issue_license(root,PASSWORD,MACHINE,1000,expires_on="31/12/2035")
    payload = verify_license(license_file.read_bytes(),json.loads(public.read_bytes()),MACHINE)
    assert payload["schema_version"] == 2
    assert payload["expires_at"] == expiry_from_date("31/12/2035")
    record = json.loads(next((root/"records").glob("*.json")).read_bytes())
    assert record["expires_at"] == payload["expires_at"]
    for value in ("31/02/2035","01/01/2000"):
        with pytest.raises(admin_tool.LicenseError):
            admin_tool.issue_license(root,PASSWORD,MACHINE,1000,expires_on=value)
    assert len(list((root/"issued").glob("*.everai-license"))) == 1


def test_admin_gui_passes_selected_date_to_issuer(tmp_path,monkeypatch):
    import tkinter as tk
    from tkinter import ttk
    from unittest.mock import Mock
    root = tk.Tk()
    root.withdraw()
    monkeypatch.setattr(tk,"Tk",lambda:root)
    monkeypatch.setattr(root,"mainloop",lambda:None)
    monkeypatch.setattr(admin_tool,"ADMIN_ROOT",tmp_path)
    from tkinter import messagebox
    monkeypatch.setattr(messagebox,"showinfo",lambda *args,**kwargs:None)
    failures = []
    monkeypatch.setattr(messagebox,"showerror",lambda *args,**kwargs:failures.append(args))
    issuer = Mock(return_value=tmp_path/"issued.everai-license")
    issuer.return_value.write_bytes(b'{"test":"signed document transport"}')
    monkeypatch.setattr(admin_tool,"issue_license",issuer)
    try:
        admin_tool.launch_gui()
        frame = root.winfo_children()[0]
        entries = [widget for widget in frame.winfo_children() if isinstance(widget,ttk.Entry)]
        for entry,value in zip(entries,[PASSWORD,MACHINE,"1000","31/12/2035","note"]):
            entry.delete(0,"end")
            entry.insert(0,value)
        buttons = next(widget for widget in frame.winfo_children() if isinstance(widget,ttk.Frame))
        next(widget for widget in buttons.winfo_children() if widget.cget("text")=="Cấp license").invoke()
        assert not failures
        issuer.assert_called_once_with(tmp_path,PASSWORD,MACHINE,1000,"note",expires_on="31/12/2035")
        copy_button = next(widget for widget in frame.winfo_children()
                           if isinstance(widget,ttk.Button) and widget.cget("text")=="Sao chép key")
        copy_button.invoke()
        assert decode_license_key(root.clipboard_get()) == issuer.return_value.read_bytes()
    finally:
        root.destroy()


@pytest.fixture
def issuer(tmp_path):
    private = Ed25519PrivateKey.generate()
    key_id = hashlib.sha256(private.public_key().public_bytes_raw()).hexdigest()[:32]
    keys = {key_id: base64.b64encode(private.public_key().public_bytes_raw()).decode()}
    def issue(amount=1000, **overrides):
        payload = dict(schema_version=1, product=PRODUCT, key_id=key_id, license_id=str(uuid.uuid4()),
                       machine_id=MACHINE, char_allowance=amount, issued_at=int(time.time()))
        payload.update(overrides)
        return canonical(dict(payload=payload, signature=base64.b64encode(private.sign(canonical(payload))).decode()))
    return keys, issue


@pytest.fixture
def wallet(tmp_path, issuer):
    keys, issue = issuer
    manager = LicenseManager(tmp_path / "app", keys=keys, machine=MACHINE)
    manager.import_license(issue())
    return manager


class MemoryTrialStore:
    def __init__(self):
        self.records = {}

    def load(self, machine):
        value = self.records.get(machine)
        return json.loads(json.dumps(value)) if value is not None else None

    def save(self, machine, record):
        self.records[machine] = json.loads(json.dumps(record))


def consume(manager, tmp_path, amount, job_id="trial-job"):
    chunk = "x" * amount
    path = tmp_path / f"{job_id}.mp3"
    with manager.job(job_id, "fingerprint-" + job_id, [chunk], [path]):
        path.write_bytes(b"ID3audio")
        manager.commit_chunk(job_id, 1, path)


def fake_concat(files, destination, **kwargs):
    destination.write_bytes(b"".join(path.read_bytes() for path in files))
    return destination


def test_signed_grants_import_once_even_with_different_json_format(tmp_path, issuer):
    keys, issue = issuer
    manager = LicenseManager(tmp_path, keys=keys, machine=MACHINE)
    data = issue(123)
    assert manager.import_license(data)
    assert not manager.import_license(json.dumps(json.loads(data), indent=4).encode())
    assert manager.balance() == dict(total=123, used=0, held=0, available=123)
    assert LicenseManager(tmp_path, keys=keys, machine=MACHINE).balance()["available"] == 123
    assert manager.import_license(issue(77))
    assert manager.balance()["total"] == 200


def test_machine_trial_survives_reinstall_and_expires_after_48_hours(tmp_path, issuer):
    keys, _ = issuer
    store = MemoryTrialStore()
    clock = SimpleNamespace(now=2_000_000_000)
    first = LicenseManager(tmp_path / "first-install", keys=keys, machine=MACHINE,
                           trial_store=store, clock=lambda: clock.now)
    balance = first.balance(include_expiry=True)
    assert balance["total"] == 10_000
    assert balance["available"] == 10_000
    assert balance["trial_expires_at"] == clock.now + 48 * 60 * 60

    consume(first, tmp_path, 2_500)
    assert store.records[MACHINE]["consumed"] == 2_500

    reopened = LicenseManager(tmp_path / "reinstalled", keys=keys, machine=MACHINE,
                              trial_store=store, clock=lambda: clock.now)
    assert reopened.balance()["used"] == 2_500
    assert reopened.balance()["available"] == 7_500
    clock.now = balance["trial_expires_at"]
    expired = reopened.balance(include_expiry=True)
    assert expired["available"] == 0
    assert expired["trial_expired"] is True
    with pytest.raises(LicenseError, match="dùng thử"):
        reopened.ensure_active()


def test_trial_clock_rollback_does_not_extend_deadline(tmp_path, issuer):
    keys, _ = issuer
    store = MemoryTrialStore()
    clock = SimpleNamespace(now=2_000_000_000)
    manager = LicenseManager(tmp_path, keys=keys, machine=MACHINE,
                             trial_store=store, clock=lambda: clock.now)
    deadline = manager.balance(include_expiry=True)["trial_expires_at"]
    clock.now += 24 * 60 * 60
    manager.balance()
    last_seen = store.records[MACHINE]["last_seen"]
    clock.now -= 23 * 60 * 60
    assert manager.balance(include_expiry=True)["trial_expires_at"] == deadline
    assert store.records[MACHINE]["last_seen"] == last_seen


def test_paid_license_is_not_charged_for_historic_trial_use(tmp_path, issuer):
    keys, issue = issuer
    store = MemoryTrialStore()
    clock = SimpleNamespace(now=int(time.time()))
    manager = LicenseManager(tmp_path, keys=keys, machine=MACHINE,
                             trial_store=store, clock=lambda: clock.now)
    consume(manager, tmp_path, 2_500)
    clock.now += 48 * 60 * 60
    manager.import_license(issue(4_000))
    balance = manager.balance(include_expiry=True)
    assert balance["available"] == 4_000
    assert balance["used"] == 2_500
    assert balance["expired"] is False
    consume(manager, tmp_path, 500, job_id="paid-after-trial")
    assert manager.balance()["available"] == 3_500


def test_corrupt_external_trial_marker_disables_trial_but_preserves_paid_balance(tmp_path, issuer):
    keys, issue = issuer
    store = MemoryTrialStore()
    clock = SimpleNamespace(now=int(time.time()))
    manager = LicenseManager(tmp_path, keys=keys, machine=MACHINE,
                             trial_store=store, clock=lambda: clock.now)
    consume(manager, tmp_path, 2_500)
    manager.import_license(issue(4_000))
    store.records[MACHINE]["allowance"] = 999_999
    reopened = LicenseManager(tmp_path, keys=keys, machine=MACHINE,
                              trial_store=store, clock=lambda: clock.now)
    balance = reopened.balance(include_expiry=True)
    assert balance["available"] == 4_000
    assert balance["trial_available"] == 0


@pytest.mark.parametrize("key_first", [True, False])
def test_pasted_key_and_file_share_one_grant_and_signed_expiry(tmp_path, timed_issuer, key_first):
    keys, issue, clock = timed_issuer
    document = issue(1234, day="31/01/2030")
    key = encode_license_key(document)
    assert key.startswith("EVKEY1-") and not any(char.isspace() for char in key)
    assert decode_license_key(key) == document
    wrapped = " \n" + "\n".join(key[i:i+80] for i in range(0, len(key), 80)) + "\t"
    manager = LicenseManager(tmp_path, keys=keys, machine=MACHINE)
    assert manager.import_license(wrapped if key_first else document)
    assert not manager.import_license(document if key_first else wrapped)
    assert manager.balance()["available"] == 1234
    reopened = LicenseManager(tmp_path, keys=keys, machine=MACHINE)
    assert reopened.balance(include_expiry=True)["expires_at"] == json.loads(document)["payload"]["expires_at"]


@pytest.mark.parametrize("key", ["", "bad-key", "EVKEY1-", "EVKEY1-!@#", "EVKEY1-A",
                                  "EVKEY1-_w", "EVKEY1-e30", "EVKEY1-" + "A"*(MAX_KEY_CHARS+1025)])
def test_invalid_pasted_key_leaves_wallet_unchanged(wallet, key):
    before = wallet.balance()
    with pytest.raises(LicenseError):
        wallet.import_license(key)
    assert wallet.balance() == before


def test_pasted_key_still_checks_signature_machine_and_expiry(tmp_path, timed_issuer):
    keys, issue, clock = timed_issuer
    manager = LicenseManager(tmp_path, keys=keys, machine=MACHINE)
    document = issue()
    for field, value in (("char_allowance", 999999), ("expires_at", clock.now+99999999)):
        changed = json.loads(document)
        changed["payload"][field] = value
        with pytest.raises(LicenseError, match="chữ ký"):
            manager.import_license(encode_license_key(canonical(changed)))
    with pytest.raises(LicenseError, match="máy khác"):
        manager.import_license(encode_license_key(issue(machine_id="EV1-"+"C"*64)))
    clock.now = json.loads(document)["payload"]["expires_at"]
    with pytest.raises(LicenseError, match="hết hạn"):
        manager.import_license(encode_license_key(document))
    assert manager.balance()["total"] == 0


def test_admin_signed_key_can_be_pasted_into_customer_wallet(tmp_path):
    owner, public = tmp_path/"owner", tmp_path/"public.json"
    admin_tool.create_keys(owner, PASSWORD, public)
    path = admin_tool.issue_license(owner, PASSWORD, MACHINE, 25000, expires_on="31/12/2035")
    key = admin_tool.encode_license_key(path.read_bytes())
    manager = LicenseManager(tmp_path/"customer", keys=json.loads(public.read_bytes()), machine=MACHINE)
    assert manager.import_license(key)
    assert not manager.import_license(path.read_bytes())
    assert manager.balance()["total"] == 25000
    assert len(key) < 1000


@pytest.mark.parametrize("changes", [dict(char_allowance=True), dict(char_allowance=-1), dict(char_allowance=0),
    dict(char_allowance=1.5), dict(char_allowance=10**13), dict(product="other"), dict(schema_version=2),
    dict(issued_at=False), dict(machine_id="invalid"), dict(license_id="not-a-uuid")])
def test_reject_invalid_signed_fields(tmp_path, issuer, changes):
    keys, issue = issuer
    manager = LicenseManager(tmp_path, keys=keys, machine=MACHINE)
    with pytest.raises(LicenseError):
        manager.import_license(issue(**changes))
    assert manager.balance()["total"] == 0


def test_reject_wrong_machine_unknown_key_tamper_and_duplicate_fields(tmp_path, issuer):
    keys, issue = issuer
    manager = LicenseManager(tmp_path, keys=keys, machine=MACHINE)
    with pytest.raises(LicenseError, match="máy khác"):
        manager.import_license(issue(machine_id="EV1-"+"C"*64))
    data = json.loads(issue())
    data["payload"]["char_allowance"] += 1
    with pytest.raises(LicenseError):
        manager.import_license(canonical(data))
    with pytest.raises(LicenseError):
        verify_license(issue(), {}, MACHINE)
    with pytest.raises(LicenseError):
        manager.import_license(b'{"payload":{},"payload":{},"signature":""}')
    with pytest.raises(LicenseError):
        manager.import_license(b"x"*(MAX_LICENSE_BYTES+1))
    with pytest.raises(LicenseError):
        manager.import_license(b'[]')


def test_same_id_different_signed_payload_rejected(wallet, issuer):
    _, issue = issuer
    license_id = str(uuid.uuid4())
    wallet.import_license(issue(12, license_id=license_id))
    with pytest.raises(LicenseError, match="nội dung khác"):
        wallet.import_license(issue(13, license_id=license_id))


def test_concurrent_duplicate_grants_credit_once(tmp_path, issuer):
    keys, issue = issuer
    a = LicenseManager(tmp_path, keys=keys, machine=MACHINE)
    b = LicenseManager(tmp_path, keys=keys, machine=MACHINE)
    data = issue(33)
    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(lambda manager: manager.import_license(data), [a,b]))
    assert sorted(results) == [False,True]
    assert a.balance()["available"] == 33


def test_wallet_rejects_wrong_machine_and_corruption(tmp_path, issuer):
    keys, _ = issuer
    manager = LicenseManager(tmp_path, keys=keys, machine=MACHINE)
    with pytest.raises(LicenseError, match="máy khác"):
        LicenseManager(tmp_path, keys=keys, machine="EV1-"+"D"*64)
    manager.path.write_bytes(b"not sqlite")
    with pytest.raises(LicenseError, match="ví ký tự"):
        LicenseManager(tmp_path, keys=keys, machine=MACHINE)


def test_reserve_commit_release_and_repeat_are_idempotent(wallet, tmp_path):
    paths = [tmp_path / "one.mp3", tmp_path / "two.mp3"]
    with wallet.job("job", "fingerprint", ["hello","world"], paths):
        assert wallet.balance()["held"] == 10
        paths[0].write_bytes(b"ID3one")
        wallet.commit_chunk("job",1,paths[0])
        wallet.commit_chunk("job",1,paths[0])
        assert wallet.balance() == dict(total=1000,used=5,held=5,available=990)
    assert wallet.balance() == dict(total=1000,used=5,held=0,available=995)
    with wallet.job("job", "fingerprint", ["hello","world"], paths):
        paths[1].write_bytes(b"ID3two")
        wallet.commit_chunk("job",2,paths[1])
    assert wallet.balance()["used"] == 10
    with pytest.raises(LicenseError, match="thay đổi"):
        with wallet.job("job", "different", ["hello","world"], paths):
            pass


def test_parallel_jobs_cannot_overdraw(wallet, tmp_path):
    ready, finish = threading.Event(), threading.Event()
    def first():
        with wallet.job("one", "one", ["x"*800], [tmp_path/"one.mp3"]):
            ready.set()
            assert finish.wait(5)
    with ThreadPoolExecutor(2) as pool:
        running = pool.submit(first)
        assert ready.wait(5)
        try:
            with pytest.raises(LicenseError, match="Không đủ"):
                with wallet.job("two", "two", ["x"*800], [tmp_path/"two.mp3"]):
                    pass
            assert wallet.balance()["available"] == 200
            with pytest.raises(LicenseError, match="cửa sổ khác"):
                with wallet.job("one", "one", ["x"*800], [tmp_path/"one.mp3"]):
                    pass
        finally:
            finish.set()
        running.result()
    assert wallet.balance()["available"] == 1000


def test_crash_after_chunk_before_commit_reconciles_on_reopen(wallet, tmp_path, issuer):
    paths = [tmp_path/"one.mp3",tmp_path/"two.mp3"]
    wallet._reserve("crashed", "fp", ["hello","there"], paths, ())
    paths[0].write_bytes(b"ID3saved-before-power-loss")
    reopened = LicenseManager(tmp_path/"app", keys=issuer[0], machine=MACHINE)
    assert reopened.balance() == dict(total=1000,used=5,held=0,available=995)
    assert reopened.recovered_chunks("crashed")[0][0] == 1
    reopened.recover_abandoned_jobs()
    assert reopened.balance()["used"] == 5


def test_live_reservations_are_not_released_by_another_window(wallet, tmp_path, issuer):
    with wallet.job("live", "fp", ["hello"], [tmp_path/"live.mp3"]):
        another = LicenseManager(tmp_path/"app", keys=issuer[0], machine=MACHINE)
        assert another.balance()["held"] == 5


def test_killed_worker_releases_os_lock_and_recovers_wallet(wallet, tmp_path, issuer):
    ready = tmp_path/"ready"
    code = """
import json, sys, time
from pathlib import Path
from src.everai.license_manager import LicenseManager
config = json.loads(sys.argv[1])
manager = LicenseManager(config['root'], keys=config['keys'], machine=config['machine'])
paths = [Path(config['path1']),Path(config['path2'])]
with manager.job('killed','fp',['hello','world'],paths):
    paths[0].write_bytes(b'ID3complete-before-kill')
    Path(config['ready']).touch()
    time.sleep(30)
"""
    config = dict(root=str(tmp_path/"app"),keys=issuer[0],machine=MACHINE,
                  path1=str(tmp_path/"first.mp3"),path2=str(tmp_path/"second.mp3"),ready=str(ready))
    process = subprocess.Popen([sys.executable,"-c",code,json.dumps(config)],
                               cwd=Path(__file__).resolve().parents[1],
                               stdout=subprocess.PIPE,stderr=subprocess.PIPE)
    try:
        deadline = time.monotonic()+8
        while not ready.exists() and process.poll() is None and time.monotonic()<deadline:
            time.sleep(.03)
        assert ready.exists(), process.poll()
        assert wallet.balance()["held"] == 10
    finally:
        if process.poll() is None:
            process.terminate()
        process.communicate(timeout=5)
    reopened = LicenseManager(tmp_path/"app",keys=issuer[0],machine=MACHINE)
    assert reopened.balance() == dict(total=1000,used=5,held=0,available=995)


def test_cancel_waits_for_workers_then_charges_only_saved_chunks(wallet,tmp_path):
    engine = EverAITTSEngine(license_manager=wallet,concurrency=1)
    calls = []
    def synthesize(index,text,total,path,*args):
        calls.append(index)
        path.write_bytes(b"ID3saved")
        engine.cancel()
        return path
    with patch.object(engine,"_process_single_chunk",side_effect=synthesize), patch.object(engine,"split_text",return_value=["hello","there"]):
        from src.everai.client import EverAICancelled
        with pytest.raises(EverAICancelled):
            engine.process("text",tmp_path/"out.mp3",job_dir=tmp_path/"job")
    assert wallet.balance()["used"] == sum(5 for _ in calls)
    assert wallet.balance()["held"] == 0
    assert not (tmp_path/"out.mp3").exists()


def test_wallet_backup_is_consistent_and_does_not_overwrite(wallet, tmp_path, issuer):
    destination = tmp_path/"wallet-backup.sqlite3"
    wallet.backup_wallet(destination)
    with sqlite3.connect(destination) as db:
        assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert db.execute("SELECT SUM(amount) FROM licenses").fetchone()[0] == 1000
    with pytest.raises(LicenseError):
        wallet.backup_wallet(destination)


def test_without_license_no_provider_request(tmp_path, issuer):
    manager = LicenseManager(tmp_path, keys=issuer[0], machine=MACHINE)
    with patch("src.everai.engine.EverAIClient") as client:
        with pytest.raises(LicenseError, match="Không đủ"):
            EverAITTSEngine(license_manager=manager).process("hello",tmp_path/"out.mp3",job_dir=tmp_path/"job")
        client.assert_not_called()


def test_engine_exact_balance_unicode_and_new_job_cost(tmp_path, issuer):
    keys, issue = issuer
    manager = LicenseManager(tmp_path, keys=keys, machine=MACHINE)
    text = "a\u0301\r\nB"
    manager.import_license(issue(4))
    client = MagicMock()
    client.synthesize_one_chunk.return_value = b"ID3audio"
    client.get_traffic_stats.return_value = {}
    with patch("src.everai.engine.EverAIClient", return_value=client), patch("src.everai.engine.concat_mp3_files", side_effect=fake_concat):
        engine = EverAITTSEngine(license_manager=manager)
        normalized = engine.split_text(text)
        assert normalized == ["á\nB"]
        engine.process(text,tmp_path/"out.mp3",job_dir=tmp_path/"one")
        assert manager.balance()["used"] == 3
        engine.process(text,tmp_path/"out.mp3",job_dir=tmp_path/"one")
        assert client.synthesize_one_chunk.call_count == 1
        with pytest.raises(LicenseError):
            engine.process(text,tmp_path/"second.mp3",job_dir=tmp_path/"two")
        assert manager.balance()["available"] == 1


def test_engine_retry_and_merge_error_resume_do_not_double_charge(wallet, tmp_path):
    client = MagicMock()
    client.synthesize_one_chunk.side_effect = [EverAIConnectionError("offline"), b"ID3hello"]
    client.get_traffic_stats.return_value = {}
    with patch("src.everai.engine.EverAIClient", return_value=client):
        with patch("src.everai.engine.concat_mp3_files", side_effect=OSError("disk")):
            with pytest.raises(OSError):
                EverAITTSEngine(license_manager=wallet,retry_delay_sec=0).process("hello",tmp_path/"out.mp3",job_dir=tmp_path/"job")
        assert wallet.balance()["used"] == 5
        with patch("src.everai.engine.concat_mp3_files", side_effect=fake_concat):
            EverAITTSEngine(license_manager=wallet).process("hello",tmp_path/"new.mp3",job_dir=tmp_path/"job")
        assert client.synthesize_one_chunk.call_count == 2
        assert wallet.balance()["used"] == 5


def test_engine_partial_failure_and_corruption_resume(wallet, tmp_path):
    client = MagicMock()
    client.get_traffic_stats.return_value = {}
    client.synthesize_one_chunk.side_effect = [b"ID3first",EverAIConnectionError("offline")]
    with patch("src.everai.engine.split_text_into_chunks",return_value=["first","second"]), patch("src.everai.engine.EverAIClient",return_value=client):
        with pytest.raises(EverAIConnectionError):
            EverAITTSEngine(license_manager=wallet,concurrency=1,max_retries_per_chunk=1).process("text",tmp_path/"out.mp3",job_dir=tmp_path/"job")
        assert wallet.balance()["used"] == 5
        assert wallet.balance()["held"] == 0
        for path in (tmp_path/"job").glob("*.mp3"):
            path.write_bytes(b"corrupted")
        client.synthesize_one_chunk.side_effect = lambda **kw: b"ID3"+kw["text"].encode()
        with patch("src.everai.engine.concat_mp3_files",side_effect=fake_concat):
            EverAITTSEngine(license_manager=wallet,concurrency=1).process("text",tmp_path/"out.mp3",job_dir=tmp_path/"job")
        assert wallet.balance()["used"] == 11


def test_engine_crash_after_commit_before_manifest_restores_chunk(wallet, tmp_path):
    client = MagicMock()
    client.synthesize_one_chunk.return_value = b"ID3hello"
    client.get_traffic_stats.return_value = {}
    original = utils.atomic_json
    def fail_manifest(path,value):
        if value.get("completed"):
            raise OSError("injected crash before manifest write")
        return original(path,value)
    with patch("src.everai.engine.EverAIClient", return_value=client):
        with patch("src.everai.engine.atomic_json",side_effect=fail_manifest):
            with pytest.raises(OSError):
                EverAITTSEngine(license_manager=wallet).process("hello",tmp_path/"out.mp3",job_dir=tmp_path/"job")
        assert wallet.balance()["used"] == 5
        with patch("src.everai.engine.concat_mp3_files",side_effect=fake_concat):
            EverAITTSEngine(license_manager=wallet).process("hello",tmp_path/"out.mp3",job_dir=tmp_path/"job")
        assert client.synthesize_one_chunk.call_count == 1
        assert wallet.balance()["used"] == 5


def test_legacy_completed_chunks_are_not_charged_retroactively(wallet, tmp_path):
    client = MagicMock()
    client.synthesize_one_chunk.return_value = b"ID3hello"
    client.get_traffic_stats.return_value = {}
    with patch("src.everai.engine.EverAIClient",return_value=client), patch("src.everai.engine.concat_mp3_files",side_effect=OSError("disk")):
        with pytest.raises(OSError):
            EverAITTSEngine(license_manager=wallet).process("hello",tmp_path/"out.mp3",job_dir=tmp_path/"job")
    # Simulate an old app's manifest/chunks arriving without a licensing job in the new wallet.
    second = LicenseManager(tmp_path/"legacy-wallet",keys=wallet.keys,machine=MACHINE)
    with patch("src.everai.engine.EverAIClient") as provider, patch("src.everai.engine.concat_mp3_files",side_effect=fake_concat):
        EverAITTSEngine(license_manager=second).process("hello",tmp_path/"out.mp3",job_dir=tmp_path/"job")
        provider.assert_not_called()
    assert second.balance()["used"] == 0


def test_admin_issue_backup_restore_and_same_customer_verification(tmp_path):
    root, public = tmp_path/"admin",tmp_path/"app-public.json"
    key_id = admin_tool.create_keys(root,PASSWORD,public)
    assert b"ENCRYPTED PRIVATE KEY" in (root/"secrets/signing_key.pem").read_bytes()
    with pytest.raises(Exception):
        admin_tool.load_private(root,"wrong password")
    with pytest.raises(Exception):
        admin_tool.create_keys(root,PASSWORD,public)
    first = admin_tool.issue_license(root,PASSWORD,MACHINE,250,"test customer",expires_on="31/12/2035")
    keys = json.loads(public.read_bytes())
    assert verify_license(first.read_bytes(),keys,MACHINE)["char_allowance"] == 250
    backup = admin_tool.backup_admin(root,tmp_path/"backup.zip",PASSWORD)
    assert admin_tool.check_backup(backup,PASSWORD) == [key_id]
    with zipfile.ZipFile(backup) as archive:
        assert not any("password" in name.lower() for name in archive.namelist())
    restored = tmp_path/"restored-admin"
    admin_tool.restore_admin(restored,backup,PASSWORD,public)
    second = admin_tool.issue_license(restored,PASSWORD,MACHINE,500,expires_on="31/12/2035")
    assert verify_license(second.read_bytes(),keys,MACHINE)["char_allowance"] == 500
    with pytest.raises(Exception):
        admin_tool.restore_admin(restored,backup,PASSWORD,public)
    with pytest.raises(Exception):
        admin_tool.check_backup(backup,"wrong password")


def test_admin_rejects_traversal_backup(tmp_path):
    backup = tmp_path/"malicious.zip"
    with zipfile.ZipFile(backup,"w") as archive:
        archive.writestr("../private.pem","bad")
    with pytest.raises(Exception):
        admin_tool.restore_admin(tmp_path/"target",backup,PASSWORD,tmp_path/"public.json")
    assert not (tmp_path/"private.pem").exists()


def test_machine_code_is_stable_and_missing_guid_fails(monkeypatch):
    import winreg
    from src.everai.license_manager import machine_id
    first = machine_id()
    assert first.startswith("EV1-") and len(first) == 68
    assert machine_id() == first
    monkeypatch.setattr(winreg,"OpenKey",MagicMock(side_effect=OSError))
    with pytest.raises(LicenseError,match="mã máy"):
        machine_id()


def test_gui_configured_preview_charges_once_and_offline_is_free(wallet,tmp_path,monkeypatch):
    import tkinter as tk
    from src.everai import gui
    utils.atomic_json(tmp_path/"proxies.json",{"items":[]})
    monkeypatch.setattr(gui,"LicenseManager",lambda root: wallet)
    monkeypatch.setattr(gui,"ensure_ffmpeg",lambda: "ffmpeg")
    root = tk.Tk()
    root.withdraw()
    app = gui.EverAIGuiApp(root,tmp_path)
    monkeypatch.setattr(app,"_load_playback",lambda *args:None)
    monkeypatch.setattr(app,"_play_pause",lambda:None)
    client = MagicMock()
    client.synthesize_one_chunk.return_value = b"ID3preview"
    client.get_traffic_stats.return_value = {}
    try:
        with patch("src.everai.engine.EverAIClient",return_value=client), patch("src.everai.engine.concat_mp3_files",side_effect=fake_concat):
            app._preview_configured()
            deadline = time.monotonic()+10
            while app.busy and time.monotonic()<deadline:
                root.update()
                time.sleep(.02)
            assert not app.busy, app.status.get()
            expected = sum(map(len,EverAITTSEngine(license_manager=wallet).split_text(gui.SAMPLE_TEXT)))
            assert wallet.balance()["used"] == expected
            app._preview_configured()
            app._preview()
            assert wallet.balance()["used"] == expected
            assert client.synthesize_one_chunk.call_count == 1
            # Cache eviction must allow a fresh, billable preview rather than a stale completed job.
            for sample in (tmp_path/"samples").glob("*.mp3"):
                sample.unlink()
            app._preview_configured()
            deadline = time.monotonic()+10
            while app.busy and time.monotonic()<deadline:
                root.update()
                time.sleep(.02)
            assert not app.busy, app.status.get()
            assert wallet.balance()["used"] == expected*2
            assert client.synthesize_one_chunk.call_count == 2
    finally:
        app._destroy()


def test_gui_import_and_copy_machine(wallet,issuer,tmp_path,monkeypatch):
    import tkinter as tk
    from src.everai import gui, license_dialog
    utils.atomic_json(tmp_path/"proxies.json",{"items":[]})
    monkeypatch.setattr(gui,"LicenseManager",lambda root:wallet)
    license_file = tmp_path/"customer.everai-license"
    license_file.write_bytes(issuer[1](25))
    monkeypatch.setattr(license_dialog.filedialog,"askopenfilename",lambda **kw:str(license_file))
    root = tk.Tk()
    root.withdraw()
    app = gui.EverAIGuiApp(root,tmp_path)
    try:
        app.license_panel.copy_machine()
        assert root.clipboard_get() == MACHINE
        app.license_panel.import_license()
        assert wallet.balance()["available"] == 1025
        app.license_panel.import_license()
        assert wallet.balance()["available"] == 1025
        assert "trước đó" in app.status.get()
        pasted = encode_license_key(issuer[1](75))
        app.license_panel.key_text.set(pasted)
        app.license_panel.activate_button.invoke()
        assert wallet.balance()["available"] == 1100
        assert app.license_panel.key_text.get() == ""
        app.license_panel.key_text.set(pasted)
        app.license_panel.activate_key()
        assert wallet.balance()["available"] == 1100
        assert "trước đó" in app.status.get()
        app.busy = "generation"
        app._set_busy(True)
        assert str(app.license_panel.import_button.cget("state")) == "disabled"
        assert str(app.license_panel.activate_button.cget("state")) == "disabled"
        assert str(app.license_panel.key_entry.cget("state")) == "disabled"
        app.license_panel.key_text.set(encode_license_key(issuer[1](100)))
        app.license_panel.activate_key()
        assert wallet.balance()["available"] == 1100
        app.busy = None
    finally:
        app._destroy()
