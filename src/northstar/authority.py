"""External, tamper-evident authority for governed repositories.

The working tree contains readable mirrors.  Runtime decisions come from a sealed
bundle outside the repository, so deleting ``.northstar/contract.yaml`` cannot turn
an already-governed checkout into an ungoverned one.

The bundle seal is an HMAC integrity check, while amendments use an Ed25519 private
key encrypted by a human passphrase. A process with unrestricted access as the same
OS user can ultimately attack key custody; SECURITY.md states that limit explicitly.
The boundary buys deterministic tamper evidence, fail-closed behaviour, and a
separate approval workflow instead of trusting YAML that the agent can rewrite.
"""

from __future__ import annotations

import getpass
import hashlib
import hmac
import json
import os
import secrets
import sys
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import yaml
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

from .contract import Amendment, Contract, ContractError
from .freeze import Oracle

SCHEMA = 1
MARKER_FILE = "project.json"
METADATA_FILE = "metadata.json"
MANIFEST_FILE = "manifest.json"
KEY_FILE = "integrity.key"
APPROVAL_KEY_FILE = "approval-key.pem"
REQUESTS_DIR = "requests"
APPROVALS_DIR = "approvals"


class IntegrityError(RuntimeError):
    """Trusted state is missing, corrupt, or no longer matches its mirrors."""


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def authority_home() -> Path:
    """OS data directory, overridable for hermetic tests and managed installs."""
    override = os.environ.get("NORTHSTAR_HOME")
    if override:
        return Path(override).expanduser().resolve()
    if os.name == "nt" and os.environ.get("LOCALAPPDATA"):
        return Path(os.environ["LOCALAPPDATA"]) / "northstar"
    if os.environ.get("XDG_DATA_HOME"):
        return Path(os.environ["XDG_DATA_HOME"]) / "northstar"
    return Path.home() / ".local" / "share" / "northstar"


def project_id(root: Path) -> str:
    """Stable identity for one checkout, even if its in-tree marker disappears."""
    identity = f"northstar-checkout-v1\0{Path(root).resolve()}"
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()[:24]


def marker_path(root: Path) -> Path:
    return Path(root) / ".northstar" / MARKER_FILE


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _canonical_json(data: Any) -> bytes:
    return json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _atomic_write(path: Path, data: bytes, mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{secrets.token_hex(6)}.tmp")
    temporary.write_bytes(data)
    try:
        temporary.chmod(mode)
    except OSError:  # Windows ACLs are not represented by POSIX mode bits.
        pass
    temporary.replace(path)


@dataclass(frozen=True)
class Authority:
    root: Path
    project: str
    path: Path

    @staticmethod
    def for_root(root: Path, home: Path | None = None) -> "Authority":
        resolved = Path(root).resolve()
        identity = project_id(resolved)
        return Authority(resolved, identity, (home or authority_home()) / "repos" / identity)

    @property
    def exists(self) -> bool:
        return (self.path / METADATA_FILE).exists()

    @property
    def contract_path(self) -> Path:
        return self.path / "contract.yaml"

    @property
    def oracle_path(self) -> Path:
        return self.path / "oracle.json"

    @property
    def journal_path(self) -> Path:
        return self.path / "journal.jsonl"

    @property
    def receipt_path(self) -> Path:
        return self.path / "receipt.json"

    @property
    def metadata_path(self) -> Path:
        return self.path / METADATA_FILE

    @property
    def manifest_path(self) -> Path:
        return self.path / MANIFEST_FILE

    @property
    def key_path(self) -> Path:
        return self.path / KEY_FILE

    @property
    def approval_key_path(self) -> Path:
        return self.path / APPROVAL_KEY_FILE

    @staticmethod
    def open(
        root: Path,
        required: bool = False,
        home: Path | None = None,
    ) -> "Authority | None":
        start = Path(root).resolve()
        if start.is_file():
            start = start.parent
        for candidate in (start, *start.parents):
            authority = Authority.for_root(candidate, home=home)
            if authority.exists:
                return authority
            marker = marker_path(candidate)
            legacy_state = candidate / ".northstar" / "contract.yaml"
            if marker.exists() or legacy_state.exists():
                raise IntegrityError(
                    "external authority is missing for a governed project; "
                    "refusing to use the working-tree copy"
                )
        if required:
            raise IntegrityError("project is not governed; run `northstar init` first")
        return None

    @staticmethod
    def bootstrap(
        root: Path,
        contract: Contract,
        oracle: Oracle,
        wiring: list[Path] | None = None,
        home: Path | None = None,
        approval_passphrase: str = "",
        authenticate_existing_amendments: bool = False,
    ) -> "Authority":
        authority = Authority.for_root(root, home=home)
        if authority.path.exists():
            raise IntegrityError(
                f"authority already exists at {authority.path}; use an approved re-baseline instead"
            )
        if len(approval_passphrase) < 12:
            raise IntegrityError("approval passphrase must contain at least 12 characters")
        if contract.amendments and not authenticate_existing_amendments:
            raise IntegrityError("initial contract contains unauthenticated amendments")
        authority.path.mkdir(parents=True, mode=0o700)
        try:
            authority.path.chmod(0o700)
        except OSError:
            pass
        _atomic_write(authority.key_path, secrets.token_bytes(32))
        private_key = Ed25519PrivateKey.generate()
        public_key = private_key.public_key().public_bytes(
            serialization.Encoding.Raw,
            serialization.PublicFormat.Raw,
        )
        encrypted_private_key = private_key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.BestAvailableEncryption(approval_passphrase.encode("utf-8")),
        )
        _atomic_write(authority.approval_key_path, encrypted_private_key)
        metadata = {
            "schema": SCHEMA,
            "project_id": authority.project,
            "root": str(authority.root),
            "created": _utcnow(),
            "approval_public_key_ed25519": public_key.hex(),
            "wiring": sorted(
                str(Path(p).resolve().relative_to(authority.root)).replace("\\", "/")
                for p in (wiring or [])
            ),
        }
        _atomic_write(authority.metadata_path, _canonical_json(metadata) + b"\n")
        _atomic_write(authority.journal_path, b"")
        if contract.amendments:
            for amendment in contract.amendments:
                amendment.approval_id = f"migration-v{amendment.version}"
                amendment.signed_by = getpass.getuser()
                amendment.signature = authority.sign_amendment(amendment, approval_passphrase)
        authority._write_bundle(contract, oracle)
        authority._write_marker()
        authority.seal()
        return authority

    def _key(self) -> bytes:
        try:
            key = self.key_path.read_bytes()
        except OSError as exc:
            raise IntegrityError("integrity key is missing or unreadable") from exc
        if len(key) != 32:
            raise IntegrityError("integrity key has an invalid length")
        return key

    def metadata(self) -> dict[str, Any]:
        try:
            data = json.loads(self.metadata_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise IntegrityError("authority metadata is missing or corrupt") from exc
        if not isinstance(data, dict):
            raise IntegrityError("authority metadata is not an object")
        return data

    def _write_marker(self) -> None:
        marker = {
            "schema": SCHEMA,
            "project_id": self.project,
            "authority": "external",
        }
        _atomic_write(marker_path(self.root), _canonical_json(marker) + b"\n")

    def _write_bundle(self, contract: Contract, oracle: Oracle) -> None:
        contract_bytes = yaml.safe_dump(
            contract.to_dict(), sort_keys=False, allow_unicode=True
        ).encode("utf-8")
        oracle_bytes = (json.dumps(oracle.to_dict(), indent=2, sort_keys=True) + "\n").encode("utf-8")
        _atomic_write(self.contract_path, contract_bytes)
        _atomic_write(self.oracle_path, oracle_bytes)
        _atomic_write(self.root / ".northstar" / "contract.yaml", contract_bytes)
        _atomic_write(self.root / ".northstar" / "oracle.json", oracle_bytes)

    def _protected_files(self) -> dict[str, Path]:
        return {
            METADATA_FILE: self.metadata_path,
            "contract.yaml": self.contract_path,
            "oracle.json": self.oracle_path,
            "journal.jsonl": self.journal_path,
            APPROVAL_KEY_FILE: self.approval_key_path,
        }

    def _mirrors(self) -> dict[str, Path]:
        return {
            MARKER_FILE: marker_path(self.root),
            "contract.yaml": self.root / ".northstar" / "contract.yaml",
            "oracle.json": self.root / ".northstar" / "oracle.json",
        }

    def seal(self) -> None:
        files: dict[str, str] = {}
        for name, path in self._protected_files().items():
            try:
                files[name] = _digest(path.read_bytes())
            except OSError as exc:
                raise IntegrityError(f"trusted artifact is missing: {name}") from exc
        mirrors: dict[str, str] = {}
        for name, path in self._mirrors().items():
            try:
                mirrors[name] = _digest(path.read_bytes())
            except OSError as exc:
                raise IntegrityError(f"working-tree mirror is missing: .northstar/{name}") from exc
        body = {
            "schema": SCHEMA,
            "project_id": self.project,
            "files": files,
            "mirrors": mirrors,
        }
        signature = hmac.new(self._key(), _canonical_json(body), hashlib.sha256).hexdigest()
        _atomic_write(self.manifest_path, _canonical_json({**body, "hmac_sha256": signature}) + b"\n")

    def verify(self, check_wiring: bool = True) -> None:
        try:
            manifest = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise IntegrityError("authority manifest is missing or corrupt") from exc
        if not isinstance(manifest, dict):
            raise IntegrityError("authority manifest is not an object")
        signature = str(manifest.pop("hmac_sha256", ""))
        expected = hmac.new(self._key(), _canonical_json(manifest), hashlib.sha256).hexdigest()
        if not signature or not hmac.compare_digest(signature, expected):
            raise IntegrityError("authority manifest signature does not verify")
        if manifest.get("schema") != SCHEMA or manifest.get("project_id") != self.project:
            raise IntegrityError("authority belongs to a different project or schema")
        metadata = self.metadata()
        if metadata.get("project_id") != self.project or Path(str(metadata.get("root", ""))).resolve() != self.root:
            raise IntegrityError("authority metadata does not match this checkout")
        for name, expected_digest in manifest.get("files", {}).items():
            path = self._protected_files().get(name)
            if path is None or not path.exists() or _digest(path.read_bytes()) != expected_digest:
                raise IntegrityError(f"trusted artifact failed integrity verification: {name}")
        for name, expected_digest in manifest.get("mirrors", {}).items():
            path = self._mirrors().get(name)
            if path is None or not path.exists() or _digest(path.read_bytes()) != expected_digest:
                raise IntegrityError(f"working-tree mirror failed integrity verification: .northstar/{name}")
        if check_wiring:
            from .install import integrity_issues

            issues = integrity_issues(self.root, [str(p) for p in metadata.get("wiring", [])])
            if issues:
                raise IntegrityError("agent wiring failed integrity verification: " + "; ".join(issues))

    def load(self, check_wiring: bool = True) -> tuple[Contract, Oracle]:
        self.verify(check_wiring=check_wiring)
        try:
            contract_data = yaml.safe_load(self.contract_path.read_text(encoding="utf-8"))
            oracle_data = json.loads(self.oracle_path.read_text(encoding="utf-8"))
            contract = Contract.from_dict(contract_data)
            oracle = Oracle.from_dict(oracle_data)
        except (OSError, json.JSONDecodeError, yaml.YAMLError, ContractError) as exc:
            raise IntegrityError(f"sealed authority payload cannot be loaded: {exc}") from exc
        for amendment in contract.amendments:
            if not amendment.approval_id or not amendment.signature:
                raise IntegrityError(f"amendment v{amendment.version} is not authenticated")
            try:
                public_bytes = bytes.fromhex(str(self.metadata()["approval_public_key_ed25519"]))
                signature = bytes.fromhex(amendment.signature)
                Ed25519PublicKey.from_public_bytes(public_bytes).verify(
                    signature,
                    self._amendment_payload(amendment),
                )
            except (InvalidSignature, ValueError, KeyError):
                raise IntegrityError(f"amendment v{amendment.version} signature does not verify")
        return contract, oracle

    def persist(
        self,
        contract: Contract,
        oracle: Oracle,
        *,
        wiring: list[Path] | None = None,
        check_wiring: bool = True,
    ) -> None:
        self.verify(check_wiring=check_wiring)
        if wiring is not None:
            metadata = self.metadata()
            metadata["wiring"] = sorted(
                str(Path(p).resolve().relative_to(self.root)).replace("\\", "/") for p in wiring
            )
            _atomic_write(self.metadata_path, _canonical_json(metadata) + b"\n")
        self._write_bundle(contract, oracle)
        self._write_marker()
        self.seal()

    def append_journal(self, line: bytes) -> None:
        self.verify()
        try:
            with self.journal_path.open("ab") as handle:
                handle.write(line)
        except OSError as exc:
            raise IntegrityError("could not append to the trusted journal") from exc
        self.seal()

    def _amendment_payload(self, amendment: Amendment) -> bytes:
        payload = amendment.to_dict()
        payload.pop("signature", None)
        return _canonical_json(payload)

    def sign_amendment(self, amendment: Amendment, passphrase: str) -> str:
        private_key = self.validate_approval_passphrase(passphrase)
        return private_key.sign(self._amendment_payload(amendment)).hex()

    def validate_approval_passphrase(self, passphrase: str) -> Ed25519PrivateKey:
        try:
            private_key = serialization.load_pem_private_key(
                self.approval_key_path.read_bytes(),
                password=passphrase.encode("utf-8"),
            )
        except (OSError, TypeError, ValueError) as exc:
            raise IntegrityError("approval passphrase is invalid") from exc
        if not isinstance(private_key, Ed25519PrivateKey):
            raise IntegrityError("approval key is not Ed25519")
        return private_key

    def create_request(self, reason: str, grants: list[str]) -> str:
        self.verify()
        if not reason.strip() or not grants:
            raise ContractError("an approval request needs a reason and at least one grant")
        request_id = uuid.uuid4().hex
        data = {
            "schema": SCHEMA,
            "request_id": request_id,
            "project_id": self.project,
            "created": _utcnow(),
            "reason": reason,
            "grants": list(grants),
        }
        _atomic_write(self.path / REQUESTS_DIR / f"{request_id}.json", _canonical_json(data) + b"\n")
        return request_id

    def approve_request(
        self,
        request_id: str,
        secret_provider: Callable[[dict[str, Any]], str],
    ) -> Amendment:
        self.verify()
        request_path = self.path / REQUESTS_DIR / f"{request_id}.json"
        approved_path = self.path / APPROVALS_DIR / f"{request_id}.json"
        if approved_path.exists():
            raise IntegrityError("approval request has already been consumed")
        try:
            request = json.loads(request_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise IntegrityError("approval request is missing or corrupt") from exc
        if request.get("project_id") != self.project or request.get("request_id") != request_id:
            raise IntegrityError("approval request does not belong to this project")
        contract, oracle = self.load()
        if any(amendment.approval_id == request_id for amendment in contract.amendments):
            raise IntegrityError("approval request has already been consumed")
        passphrase = secret_provider(request)
        if not isinstance(passphrase, str) or not passphrase:
            raise IntegrityError("approval was not confirmed by a human channel")
        amendment = contract.amend(
            str(request["reason"]),
            [str(g) for g in request["grants"]],
            signed_by=getpass.getuser(),
            approval_id=request_id,
        )
        amendment.signature = self.sign_amendment(amendment, passphrase)
        self.persist(contract, oracle)
        approval = {
            **request,
            "approved": _utcnow(),
            "signed_by": amendment.signed_by,
            "amendment_version": amendment.version,
            "signature": amendment.signature,
        }
        _atomic_write(approved_path, _canonical_json(approval) + b"\n")
        request_path.unlink(missing_ok=True)
        return amendment


def prompt_new_approval_secret() -> str:
    """Create the secret that encrypts the human approval signing key."""
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        raise IntegrityError("initialisation requires an interactive human terminal")
    first = getpass.getpass("Create approval passphrase (12+ characters): ")
    second = getpass.getpass("Repeat approval passphrase: ")
    if first != second:
        raise IntegrityError("approval passphrases do not match")
    if len(first) < 12:
        raise IntegrityError("approval passphrase must contain at least 12 characters")
    return first


def interactive_confirmation(request: dict[str, Any]) -> str:
    """Read the Ed25519 decryption secret only from a separate human terminal."""
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        raise IntegrityError("approval requires a separate interactive terminal")
    print("Northstar approval request")
    print(f"  reason: {request['reason']}")
    for grant in request["grants"]:
        print(f"  grant:  {grant}")
    return getpass.getpass("Approval passphrase: ")


def governed(root: Path) -> bool:
    """True even when the in-tree marker was deleted, while authority survives."""
    return Authority.for_root(root).exists or marker_path(root).exists
