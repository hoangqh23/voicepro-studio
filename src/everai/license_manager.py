"""Offline signed grants and a transactional character wallet (no network calls)."""
import base64
import hashlib
import json
import os
import re
import sqlite3
import sys
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

if hasattr(sys, 'getandroidapilevel'):
    from .android_crypto import InvalidSignature, Ed25519PublicKey
else:
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

try:
    from .utils import application_data_dir
except ImportError:
    from utils import application_data_dir

PRODUCT = "everai-studio"
MAX_GRANT = 10**12
MAX_LICENSE_BYTES = 16384
KEY_PREFIX = "EVKEY1-"
MAX_KEY_CHARS = len(KEY_PREFIX) + 4 * ((MAX_LICENSE_BYTES + 2) // 3)
MACHINE_PATTERN = r"EV1-[A-F0-9]{64}"
LICENSE_TIMEZONE = timezone(timedelta(hours=7), name="UTC+07:00")
TRIAL_ALLOWANCE = 10_000
TRIAL_DURATION_SECONDS = 2 * 24 * 60 * 60
TRIAL_REGISTRY_PATH = r"Software\VoicePro\Trial"


class LicenseError(ValueError):
    """A licensing error safe to display without exposing credentials."""


class WindowsTrialStore:
    """Machine-bound marker outside the install/data directory for reinstall recovery."""

    @staticmethod
    def _name(machine):
        return hashlib.sha256(machine.encode("ascii")).hexdigest()

    def load(self, machine):
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, TRIAL_REGISTRY_PATH) as key:
                value, kind = winreg.QueryValueEx(key, self._name(machine))
            if kind != winreg.REG_SZ:
                raise ValueError("invalid registry value")
            return strict_json(value)
        except FileNotFoundError:
            return None
        except (ImportError, OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
            raise LicenseError("Không đọc được trạng thái dùng thử của máy.") from exc

    def save(self, machine, record):
        try:
            import winreg
            with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, TRIAL_REGISTRY_PATH, 0,
                                    winreg.KEY_SET_VALUE) as key:
                winreg.SetValueEx(key, self._name(machine), 0, winreg.REG_SZ,
                                  canonical(record).decode("utf-8"))
        except (ImportError, OSError, ValueError, TypeError) as exc:
            raise LicenseError("Không lưu được trạng thái dùng thử của máy.") from exc


class FileTrialStore:
    """macOS marker kept outside the app data directory, mirroring the Windows registry store."""

    def __init__(self, directory=None):
        self.directory = Path(directory or Path.home() / "Library" / "Application Support" / ".VoicePro-trial")

    def _path(self, machine):
        return self.directory / (hashlib.sha256(machine.encode("ascii")).hexdigest() + ".json")

    def load(self, machine):
        try:
            return strict_json(self._path(machine).read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None
        except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
            raise LicenseError("Không đọc được trạng thái dùng thử của máy.") from exc

    def save(self, machine, record):
        try:
            self.directory.mkdir(parents=True, exist_ok=True)
            target = self._path(machine)
            temporary = target.with_suffix(".tmp")
            temporary.write_text(canonical(record).decode("utf-8"), encoding="utf-8")
            os.replace(temporary, target)
        except (OSError, ValueError, TypeError) as exc:
            raise LicenseError("Không lưu được trạng thái dùng thử của máy.") from exc


def expiry_from_date(value):
    """The selected Vietnam date is inclusive; store next midnight as an exclusive boundary."""
    for pattern in ("%d/%m/%Y", "%Y-%m-%d"):
        try:
            day = datetime.strptime(value.strip(), pattern).replace(tzinfo=LICENSE_TIMEZONE)
            return int((day + timedelta(days=1)).timestamp())
        except (ValueError, OverflowError, OSError):
            continue
    raise LicenseError("Ngày hết hạn không hợp lệ. Nhập DD/MM/YYYY, ví dụ 31/12/2026.")


def expiry_label(expires_at):
    if expires_at is None:
        return "Không giới hạn ngày (key cũ)"
    return datetime.fromtimestamp(expires_at-1, LICENSE_TIMEZONE).strftime("%d/%m/%Y")


def machine_id():
    if hasattr(sys, 'getandroidapilevel'):
        from java import jclass
        value = str(jclass('com.everaistudio.mobile.NativeBridge').deviceId())
        if not value:
            raise LicenseError('Không đọc được mã thiết bị Android.')
        return 'EV1-' + hashlib.sha256((PRODUCT + ':android:v1:' + value).encode()).hexdigest().upper()
    if sys.platform == "darwin":
        import subprocess
        try:
            output = subprocess.run(["/usr/sbin/ioreg", "-rd1", "-c", "IOPlatformExpertDevice"],
                                    capture_output=True, text=True, check=True, timeout=10).stdout
            match = re.search(r'"IOPlatformUUID"\s*=\s*"([0-9A-Fa-f-]{36})"', output)
            if not match:
                raise ValueError()
        except (OSError, ValueError, subprocess.SubprocessError) as exc:
            raise LicenseError("Không đọc được mã máy Mac. Liên hệ quản trị viên.") from exc
        return "EV1-" + hashlib.sha256((PRODUCT + ":mac:v1:" + match.group(1).lower()).encode()).hexdigest().upper()
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Cryptography",
                            0, winreg.KEY_READ | winreg.KEY_WOW64_64KEY) as key:
            value = winreg.QueryValueEx(key, "MachineGuid")[0].strip().lower()
        if not value:
            raise ValueError()
    except (ImportError, OSError, ValueError) as exc:
        raise LicenseError("Không đọc được mã máy Windows. Liên hệ quản trị viên.") from exc
    return "EV1-" + hashlib.sha256((PRODUCT + ":v1:" + value).encode()).hexdigest().upper()


def public_keys_path():
    return Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent)) / "assets/license/public_keys.json"


def strict_json(data):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise LicenseError("License chứa trường trùng lặp.")
            result[key] = value
        return result
    return json.loads(data, object_pairs_hook=pairs)


def canonical(payload):
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                      allow_nan=False).encode("utf-8")


def encode_license_key(document):
    """Carry the existing signed document in one copyable, URL-safe string."""
    if len(document) > MAX_LICENSE_BYTES:
        raise LicenseError("License quá lớn.")
    return KEY_PREFIX + base64.urlsafe_b64encode(document).rstrip(b"=").decode("ascii")


def decode_license_key(key):
    if not isinstance(key, str) or len(key) > MAX_KEY_CHARS + 1024:
        raise LicenseError("Key quá dài hoặc không hợp lệ.")
    key = "".join(key.split())
    if len(key) > MAX_KEY_CHARS or not key.startswith(KEY_PREFIX):
        raise LicenseError("Key không đúng định dạng. Hãy dán toàn bộ mã EVKEY1-…")
    encoded = key[len(KEY_PREFIX):]
    if not re.fullmatch(r"[A-Za-z0-9_-]+", encoded):
        raise LicenseError("Key chứa ký tự không hợp lệ.")
    try:
        document = base64.b64decode(encoded + "=" * (-len(encoded) % 4), altchars=b"-_", validate=True)
    except ValueError as exc:
        raise LicenseError("Key bị thiếu hoặc không hợp lệ.") from exc
    if len(document) > MAX_LICENSE_BYTES:
        raise LicenseError("License quá lớn.")
    return document


def validate_payload(payload):
    fields = {"schema_version", "product", "key_id", "license_id", "machine_id", "char_allowance", "issued_at"}
    if not isinstance(payload, dict):
        raise LicenseError("Định dạng license không hợp lệ.")
    version = payload.get("schema_version")
    if type(version) is not int or version not in (1, 2):
        raise LicenseError("License sai sản phẩm hoặc phiên bản.")
    if set(payload) != fields | ({"expires_at"} if version == 2 else set()):
        raise LicenseError("Định dạng license không hợp lệ.")
    if payload["product"] != PRODUCT:
        raise LicenseError("License sai sản phẩm hoặc phiên bản.")
    if type(payload["char_allowance"]) is not int or not 0 < payload["char_allowance"] <= MAX_GRANT:
        raise LicenseError("Số ký tự của license không hợp lệ.")
    if type(payload["issued_at"]) is not int or payload["issued_at"] <= 0:
        raise LicenseError("Thời điểm cấp license không hợp lệ.")
    if version == 2:
        if type(payload["expires_at"]) is not int or payload["expires_at"] <= payload["issued_at"]:
            raise LicenseError("Thời hạn license không hợp lệ.")
        try:
            datetime.fromtimestamp(payload["expires_at"], LICENSE_TIMEZONE)
        except (ValueError, OverflowError, OSError) as exc:
            raise LicenseError("Thời hạn license nằm ngoài khoảng ngày hợp lệ.") from exc
    if not isinstance(payload["machine_id"], str) or not re.fullmatch(MACHINE_PATTERN, payload["machine_id"]):
        raise LicenseError("Mã máy không hợp lệ.")
    if not isinstance(payload["key_id"], str) or not re.fullmatch(r"[a-f0-9]{32}", payload["key_id"]):
        raise LicenseError("Mã khóa không hợp lệ.")
    try:
        if str(uuid.UUID(payload["license_id"])) != payload["license_id"]:
            raise ValueError()
    except (ValueError, TypeError, AttributeError) as exc:
        raise LicenseError("Mã license không hợp lệ.") from exc


def verify_license(data, keys, expected_machine):
    try:
        if len(data) > MAX_LICENSE_BYTES:
            raise LicenseError("File license quá lớn.")
        envelope = strict_json(data)
        if not isinstance(envelope, dict) or set(envelope) != {"payload", "signature"}:
            raise LicenseError("File license không đúng định dạng.")
        payload = envelope["payload"]
        validate_payload(payload)
        raw_key = base64.b64decode(keys[payload["key_id"]], validate=True)
        signature = base64.b64decode(envelope["signature"], validate=True)
        Ed25519PublicKey.from_public_bytes(raw_key).verify(signature, canonical(payload))
        if payload["machine_id"] != expected_machine:
            raise LicenseError("License này được cấp cho máy khác.")
        return payload
    except LicenseError:
        raise
    except (ValueError, TypeError, KeyError, InvalidSignature, UnicodeError) as exc:
        raise LicenseError("License không hợp lệ hoặc chữ ký không đúng.") from exc


class LicenseManager:
    def __init__(self, root=None, *, keys=None, machine=None, trial_store=None, clock=None):
        self.root = Path(root or application_data_dir()) / "licensing"
        self.root.mkdir(parents=True, exist_ok=True)
        detected_machine = machine is None
        self.machine = machine or machine_id()
        self.clock = clock or (lambda: time.time())
        self.trial_store = trial_store
        if self.trial_store is None and detected_machine and os.name == "nt" and not hasattr(sys, 'getandroidapilevel'):
            self.trial_store = WindowsTrialStore()
        elif self.trial_store is None and detected_machine and sys.platform == "darwin":
            self.trial_store = FileTrialStore()
        self.trial_error = ""
        try:
            self.keys = keys if keys is not None else strict_json(public_keys_path().read_bytes())
            if not isinstance(self.keys, dict):
                raise ValueError()
        except (OSError, ValueError) as exc:
            raise LicenseError("Thiếu hoặc hỏng khóa công khai của ứng dụng.") from exc
        self.path = self.root / "wallet.sqlite3"
        with self._db() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS meta (name TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS licenses
                    (id TEXT PRIMARY KEY, document BLOB NOT NULL, digest TEXT NOT NULL, amount INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS chunks
                    (job TEXT NOT NULL REFERENCES jobs(id), idx INTEGER NOT NULL, cost INTEGER NOT NULL,
                     state TEXT NOT NULL CHECK(state IN ('pending','held','paid','reused')),
                     path TEXT NOT NULL, sha256 TEXT, trial_cost INTEGER NOT NULL DEFAULT 0,
                     PRIMARY KEY(job,idx));
                CREATE TABLE IF NOT EXISTS ledger
                    (seq INTEGER PRIMARY KEY, event TEXT NOT NULL, reference TEXT NOT NULL,
                     amount INTEGER NOT NULL, created TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
            """)
            if "trial_cost" not in {row[1] for row in db.execute("PRAGMA table_info(chunks)")}:
                db.execute("ALTER TABLE chunks ADD COLUMN trial_cost INTEGER NOT NULL DEFAULT 0")
            db.execute("INSERT OR IGNORE INTO meta VALUES ('machine',?)", (self.machine,))
            if db.execute("SELECT value FROM meta WHERE name='machine'").fetchone()[0] != self.machine:
                raise LicenseError("Ví ký tự thuộc máy khác. Liên hệ quản trị viên.")
            if db.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                raise LicenseError("Ví ký tự bị hỏng. Hãy khôi phục bản sao lưu.")
            self._initialize_trial(db)
        self.recover_abandoned_jobs()

    @staticmethod
    def _validate_trial(record, machine):
        required = {"version", "machine", "started_at", "expires_at", "last_seen", "allowance", "consumed"}
        if not isinstance(record, dict) or set(record) != required:
            raise LicenseError("Trạng thái dùng thử không hợp lệ.")
        if record["version"] != 1 or record["machine"] != machine:
            raise LicenseError("Trạng thái dùng thử không thuộc máy này.")
        for field in ("started_at", "expires_at", "last_seen", "allowance"):
            if type(record[field]) is not int or record[field] <= 0:
                raise LicenseError("Trạng thái dùng thử không hợp lệ.")
        if record["allowance"] != TRIAL_ALLOWANCE:
            raise LicenseError("Hạn mức dùng thử không hợp lệ.")
        if type(record["consumed"]) is not int or not 0 <= record["consumed"] <= TRIAL_ALLOWANCE:
            raise LicenseError("Mức sử dụng dùng thử không hợp lệ.")
        if record["expires_at"] != record["started_at"] + TRIAL_DURATION_SECONDS:
            raise LicenseError("Thời hạn dùng thử không hợp lệ.")
        if record["last_seen"] < record["started_at"]:
            raise LicenseError("Thời gian dùng thử không hợp lệ.")
        return dict(record)

    def _wallet_trial(self, db):
        row = db.execute("SELECT value FROM meta WHERE name='trial_v1'").fetchone()
        if not row:
            return None
        try:
            return self._validate_trial(strict_json(row[0]), self.machine)
        except (ValueError, TypeError, json.JSONDecodeError) as exc:
            raise LicenseError("Trạng thái dùng thử trong ví không hợp lệ.") from exc

    def _save_trial(self, db, record, *, registry=True):
        record = self._validate_trial(record, self.machine)
        db.execute("INSERT OR REPLACE INTO meta VALUES ('trial_v1',?)",
                   (canonical(record).decode("utf-8"),))
        if registry and self.trial_store is not None:
            self.trial_store.save(self.machine, record)

    @staticmethod
    def _trial_baseline(db):
        row = db.execute("SELECT value FROM meta WHERE name='trial_baseline_v1'").fetchone()
        if not row:
            return None
        try:
            value = int(row[0])
        except (TypeError, ValueError) as exc:
            raise LicenseError("Mốc sử dụng dùng thử trong ví không hợp lệ.") from exc
        if not 0 <= value <= TRIAL_ALLOWANCE:
            raise LicenseError("Mốc sử dụng dùng thử trong ví không hợp lệ.")
        return value

    @staticmethod
    def _trial_db_used(db):
        row = db.execute("SELECT value FROM meta WHERE name='trial_db_used_v1'").fetchone()
        if not row:
            return None
        try:
            value = int(row[0])
        except (TypeError, ValueError) as exc:
            raise LicenseError("Mốc sử dụng dùng thử trong dữ liệu tác vụ không hợp lệ.") from exc
        if not 0 <= value <= TRIAL_ALLOWANCE:
            raise LicenseError("Mốc sử dụng dùng thử trong dữ liệu tác vụ không hợp lệ.")
        return value

    @staticmethod
    def _save_trial_accounting(db, baseline, db_used):
        db.execute("INSERT OR REPLACE INTO meta VALUES ('trial_baseline_v1',?)", (str(baseline),))
        db.execute("INSERT OR REPLACE INTO meta VALUES ('trial_db_used_v1',?)", (str(db_used),))

    def _initialize_trial(self, db):
        if self.trial_store is None:
            return
        try:
            wallet = self._wallet_trial(db)
            external = self.trial_store.load(self.machine)
            if external is not None:
                external = self._validate_trial(external, self.machine)
            now = int(self.clock())
            if wallet is None and external is None:
                selected = dict(version=1, machine=self.machine, started_at=now,
                                expires_at=now + TRIAL_DURATION_SECONDS,
                                last_seen=now, allowance=TRIAL_ALLOWANCE, consumed=0)
            else:
                records = [item for item in (wallet, external) if item is not None]
                started = min(item["started_at"] for item in records)
                selected = dict(version=1, machine=self.machine, started_at=started,
                                expires_at=started + TRIAL_DURATION_SECONDS,
                                last_seen=max([now] + [item["last_seen"] for item in records]),
                                allowance=TRIAL_ALLOWANCE,
                                consumed=max(item["consumed"] for item in records))
            db_used = db.execute("SELECT COALESCE(SUM(cost),0) FROM chunks WHERE state='paid'").fetchone()[0]
            baseline = self._trial_baseline(db)
            trial_db_used = self._trial_db_used(db)
            if wallet is None:
                baseline, trial_db_used = selected["consumed"], 0
            else:
                if baseline is None:
                    baseline = max(0, selected["consumed"] - db_used)
                if trial_db_used is None:
                    trial_db_used = min(db_used, max(0, selected["consumed"] - baseline))
                baseline = max(baseline, selected["consumed"] - trial_db_used)
            baseline = min(TRIAL_ALLOWANCE, baseline)
            trial_db_used = min(TRIAL_ALLOWANCE - baseline, trial_db_used)
            selected["consumed"] = max(selected["consumed"], baseline + trial_db_used)
            self._save_trial_accounting(db, baseline, trial_db_used)
            self._save_trial(db, selected)
        except LicenseError as exc:
            # A damaged trial marker must never mint a replacement or invalidate paid grants.
            self.trial_error = str(exc)

    def _trial(self, db):
        if self.trial_store is None:
            return None
        try:
            record = self._wallet_trial(db)
        except LicenseError:
            return None
        if record is None:
            self.trial_error = "Không tìm thấy trạng thái dùng thử của máy."
            return None
        now = int(self.clock())
        baseline = self._trial_baseline(db)
        trial_db_used = self._trial_db_used(db)
        if baseline is None or trial_db_used is None:
            self.trial_error = "Không tìm thấy mốc sử dụng dùng thử của ví."
            return None
        record["consumed"] = max(record["consumed"], min(TRIAL_ALLOWANCE, baseline + trial_db_used))
        record["last_seen"] = max(record["last_seen"], now)
        if not self.trial_error:
            try:
                self._save_trial(db, record)
            except LicenseError as exc:
                self.trial_error = str(exc)
        # A broken external marker disables free use, while the valid wallet copy
        # still partitions historic trial usage from paid-license usage.
        record["effective_now"] = record["expires_at"] if self.trial_error else max(now, record["last_seen"])
        record["baseline"] = baseline
        record["db_used"] = trial_db_used
        return record

    @contextmanager
    def _db(self):
        db = None
        try:
            db = sqlite3.connect(self.path, timeout=15, isolation_level=None)
            db.execute("PRAGMA foreign_keys=ON")
            db.execute("PRAGMA synchronous=FULL")
            yield db
        except sqlite3.Error as exc:
            raise LicenseError("Không đọc/ghi được ví ký tự. Kiểm tra ổ đĩa hoặc khôi phục backup.") from exc
        finally:
            if db is not None:
                if db.in_transaction:
                    db.rollback()
                db.close()

    def _balance(self, db, include_expiry=False):
        paid_total = 0
        expires_at = None
        for document, digest, amount in db.execute("SELECT document,digest,amount FROM licenses"):
            payload = verify_license(document, self.keys, self.machine)
            if hashlib.sha256(document).hexdigest() != digest or payload["char_allowance"] != amount:
                raise LicenseError("Dữ liệu license trong ví không hợp lệ.")
            paid_total += amount
            # Dated grants govern the app as a whole; an older key cannot shorten or remove the deadline.
            if payload["schema_version"] == 2:
                expires_at = max(expires_at or 0, payload["expires_at"])
        used, held, used_trial, held_trial = db.execute(
            "SELECT COALESCE(SUM(CASE WHEN state='paid' THEN cost ELSE 0 END),0),"
            "COALESCE(SUM(CASE WHEN state='held' THEN cost ELSE 0 END),0),"
            "COALESCE(SUM(CASE WHEN state='paid' THEN trial_cost ELSE 0 END),0),"
            "COALESCE(SUM(CASE WHEN state='held' THEN trial_cost ELSE 0 END),0) FROM chunks").fetchone()
        trial = self._trial(db)
        trial_total = trial["allowance"] if trial else 0
        baseline = trial.get("baseline", 0) if trial else 0
        trial_db_used = trial.get("db_used", 0) if trial else 0
        consumed = baseline + used + held
        if (used < 0 or held < 0 or used_trial < 0 or held_trial < 0 or used_trial > used or
                held_trial > held or trial_db_used > used or consumed > paid_total + trial_total):
            raise LicenseError("Số dư ví không hợp lệ. Liên hệ quản trị viên.")
        now = int(self.clock())
        trial_active = bool(trial and trial["effective_now"] < trial["expires_at"])
        paid_expired = expires_at is not None and now >= expires_at
        paid_spent = max(0, used - trial_db_used)
        trial_remaining = max(0, trial_total - (trial["consumed"] if trial else 0))
        paid_remaining = max(0, paid_total - paid_spent)
        trial_available = max(0, trial_remaining - held_trial) if trial_active else 0
        paid_available = max(0, paid_remaining - (held - held_trial)) if not paid_expired else 0
        available = trial_available + paid_available
        remaining = trial_remaining + paid_remaining
        paid_active = paid_total > 0 and not paid_expired
        expired = bool((trial is not None and not trial_active and not paid_active) or
                       (trial is None and paid_total > 0 and paid_expired))
        total = paid_total + trial_total
        result = dict(total=total, used=baseline + used, held=held, available=available)
        if include_expiry:
            display_expiry = expires_at
            if paid_total == 0 and trial is not None:
                display_expiry = trial["expires_at"]
            result.update(expires_at=display_expiry, expired=expired, remaining=remaining)
            if trial is not None:
                result.update(trial=True, trial_expires_at=trial["expires_at"],
                              trial_available=trial_available,
                              trial_expired=not trial_active)
        return result

    def balance(self, include_expiry=False):
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            result = self._balance(db, include_expiry=include_expiry)
            db.commit()
            return result

    @staticmethod
    def _require_active(balance):
        if balance["expired"]:
            if balance.get("trial_expired"):
                raise LicenseError("Gói dùng thử 10.000 ký tự trong 2 ngày đã hết hạn. Hãy kích hoạt license để tiếp tục.")
            raise LicenseError(f"License đã hết hạn ngày {expiry_label(balance['expires_at'])} (giờ Việt Nam). "
                               "Hãy nhập license gia hạn để tiếp tục; số dư ký tự vẫn được giữ.")

    def ensure_active(self):
        self._require_active(self.balance(include_expiry=True))

    def import_license(self, data):
        if isinstance(data, str):
            data = decode_license_key(data)
        payload = verify_license(data, self.keys, self.machine)
        data = canonical(strict_json(data))
        digest = hashlib.sha256(data).hexdigest()
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            previous = db.execute("SELECT digest FROM licenses WHERE id=?", (payload["license_id"],)).fetchone()
            if previous:
                if previous[0] != digest:
                    raise LicenseError("Mã license đã tồn tại với nội dung khác.")
                return False
            if payload.get("expires_at", float("inf")) <= time.time():
                raise LicenseError("License này đã hết hạn. Hãy yêu cầu quản trị viên cấp key mới.")
            db.execute("INSERT INTO licenses VALUES (?,?,?,?)",
                       (payload["license_id"], data, digest, payload["char_allowance"]))
            db.execute("INSERT INTO ledger(event,reference,amount) VALUES ('grant',?,?)",
                       (payload["license_id"], payload["char_allowance"]))
            db.commit()
        return True

    @contextmanager
    def _job_lock(self, job_id):
        """OS lock survives threads/windows, but is automatically released on process death."""
        locks = self.root / "locks"
        locks.mkdir(exist_ok=True)
        lock_path = locks / (hashlib.sha256(job_id.encode()).hexdigest() + ".lock")
        with lock_path.open("a+b") as handle:
            handle.seek(0, 2)
            if handle.tell() == 0:
                handle.write(b"0")
                handle.flush()
            handle.seek(0)
            try:
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as exc:
                raise LicenseError("Tác vụ này đang chạy trong cửa sổ khác.") from exc
            try:
                yield
            finally:
                handle.seek(0)
                if os.name == "nt":
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(handle, fcntl.LOCK_UN)

    @contextmanager
    def job(self, job_id, fingerprint, chunks, paths, existing=()):
        with self._job_lock(job_id):
            self._reserve(job_id, fingerprint, chunks, paths, existing)
            try:
                yield
            finally:
                self.release(job_id)

    def recover_abandoned_jobs(self):
        with self._db() as db:
            jobs = [row[0] for row in db.execute("SELECT DISTINCT job FROM chunks WHERE state='held'")]
        for job_id in jobs:
            # Acquisition alone can fail for a live job. Recovery errors must still reach the user.
            context = self._job_lock(job_id)
            try:
                context.__enter__()
            except LicenseError:
                continue
            try:
                self.release(job_id)
            finally:
                context.__exit__(None, None, None)

    def _recover_held(self, db, job_id):
        for index, path in db.execute("SELECT idx,path FROM chunks WHERE job=? AND state='held'", (job_id,)).fetchall():
            try:
                content = Path(path).read_bytes()
            except FileNotFoundError:
                content = b""
            valid = content.startswith(b"ID3") or (len(content)>1 and content[0]==255 and content[1]&224==224)
            if valid:
                self._commit(db, job_id, index, content)

    def _reserve(self, job_id, fingerprint, chunks, paths, existing):
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            previous = db.execute("SELECT fingerprint FROM jobs WHERE id=?", (job_id,)).fetchone()
            if previous and previous[0] != fingerprint:
                raise LicenseError("Nội dung hoặc cấu hình tác vụ đã thay đổi.")
            db.execute("INSERT OR IGNORE INTO jobs VALUES (?,?)", (job_id, fingerprint))
            for index, (chunk, path) in enumerate(zip(chunks, paths), 1):
                state = "reused" if not previous and index in existing else "pending"
                db.execute("INSERT OR IGNORE INTO chunks(job,idx,cost,state,path,sha256) VALUES (?,?,?,?,?,NULL)",
                           (job_id, index, len(chunk), state, str(path)))
            rows = db.execute("SELECT idx,cost,state,path FROM chunks WHERE job=?", (job_id,)).fetchall()
            if len(rows) != len(chunks) or any(cost != len(chunks[i-1]) or path != str(paths[i-1])
                                             for i, cost, _, path in rows):
                raise LicenseError("Danh sách phân đoạn của tác vụ không khớp.")
            self._recover_held(db, job_id)
            db.execute("UPDATE chunks SET state='pending' WHERE job=? AND state='held'", (job_id,))
            balance = self._balance(db, include_expiry=True)
            pending = db.execute("SELECT idx,cost FROM chunks WHERE job=? AND state='pending'", (job_id,)).fetchall()
            if pending:
                self._require_active(balance)
            required = sum(cost for _, cost in pending)
            if required > balance["available"]:
                raise LicenseError(f"Không đủ ký tự: cần {required:,}, còn {balance['available']:,}. Hãy nhập thêm license.")
            trial_capacity = balance.get("trial_available", 0)
            for index, cost in pending:
                trial_cost = min(cost, trial_capacity)
                db.execute("UPDATE chunks SET trial_cost=? WHERE job=? AND idx=?",
                           (trial_cost, job_id, index))
                trial_capacity -= trial_cost
            db.execute("UPDATE chunks SET state='held' WHERE job=? AND state='pending'", (job_id,))
            db.execute("INSERT INTO ledger(event,reference,amount) VALUES ('reserve',?,?)", (job_id,required))
            db.commit()

    def _commit(self, db, job_id, index, content):
        row = db.execute("SELECT cost,state,trial_cost FROM chunks WHERE job=? AND idx=?", (job_id,index)).fetchone()
        if row is None or row[1] == "pending":
            raise LicenseError("Chưa giữ hạn mức cho phân đoạn.")
        if row[1] == "held":
            db.execute("UPDATE chunks SET state='paid',sha256=? WHERE job=? AND idx=?",
                       (hashlib.sha256(content).hexdigest(),job_id,index))
            db.execute("INSERT INTO ledger(event,reference,amount) VALUES ('consume',?,?)",
                       (f"{job_id}:{index}",row[0]))
            used = db.execute("SELECT COALESCE(SUM(cost),0) FROM chunks WHERE state='paid'").fetchone()[0]
            trial_cost = row[2]
            if trial_cost:
                baseline = self._trial_baseline(db) or 0
                trial_db_used = (self._trial_db_used(db) or 0) + trial_cost
                self._save_trial_accounting(db, baseline, trial_db_used)
            self._trial(db)

    def commit_chunk(self, job_id, index, path):
        content = Path(path).read_bytes()
        if not content:
            raise LicenseError("Không trừ ký tự cho file âm thanh rỗng.")
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            self._commit(db,job_id,index,content)
            db.commit()

    def recovered_chunks(self, job_id):
        with self._db() as db:
            return db.execute("SELECT idx,path,sha256 FROM chunks WHERE job=? AND state='paid' AND sha256 IS NOT NULL",
                              (job_id,)).fetchall()

    def release(self, job_id):
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            self._recover_held(db, job_id)
            amount = db.execute("SELECT COALESCE(SUM(cost),0) FROM chunks WHERE job=? AND state='held'", (job_id,)).fetchone()[0]
            db.execute("UPDATE chunks SET state='pending' WHERE job=? AND state='held'", (job_id,))
            db.execute("INSERT INTO ledger(event,reference,amount) VALUES ('release',?,?)", (job_id,amount))
            db.commit()

    def backup_wallet(self, destination):
        destination = Path(destination)
        if destination.exists():
            raise LicenseError("File backup đã tồn tại. Hãy chọn tên khác.")
        with self._db() as db, sqlite3.connect(destination) as target:
            db.backup(target)
