from pathlib import Path
from typing import Dict, List, Optional
import json

from .models import BatchHistory, Profile, ProfileNameConflictError, AuditLogEntry, PackageRecord


class BatchStorage:
    def __init__(self, work_dir: Path):
        self.work_dir = Path(work_dir)
        self.batches_dir = self.work_dir / "batches"
        self.batches_dir.mkdir(parents=True, exist_ok=True)
        self.index_file = self.batches_dir / "index.json"

    def _get_batch_path(self, batch_id: str) -> Path:
        return self.batches_dir / f"{batch_id}.json"

    def save(self, batch: BatchHistory) -> None:
        from datetime import datetime
        batch.updated_at = datetime.now()
        batch_path = self._get_batch_path(batch.batch_id)
        with open(batch_path, "w", encoding="utf-8") as f:
            json.dump(batch.to_dict(), f, ensure_ascii=False, indent=2)
        self._update_index(batch)

    def load(self, batch_id: str) -> Optional[BatchHistory]:
        batch_path = self._get_batch_path(batch_id)
        if not batch_path.exists():
            return None
        with open(batch_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return BatchHistory.from_dict(data)

    def list_batches(self) -> List[Dict]:
        if not self.index_file.exists():
            return []
        with open(self.index_file, "r", encoding="utf-8") as f:
            return json.load(f)

    def _update_index(self, batch: BatchHistory) -> None:
        batches = self.list_batches()
        existing = next((b for b in batches if b["batch_id"] == batch.batch_id), None)
        entry = {
            "batch_id": batch.batch_id,
            "name": batch.name,
            "created_at": batch.created_at.isoformat(),
            "updated_at": batch.updated_at.isoformat(),
            "file_count": len(batch.scanned_files),
            "delivery_count": len(batch.delivery_list),
            "correction_count": len(batch.corrections),
        }
        if existing:
            existing.update(entry)
        else:
            batches.append(entry)
        with open(self.index_file, "w", encoding="utf-8") as f:
            json.dump(batches, f, ensure_ascii=False, indent=2)

    def delete(self, batch_id: str) -> bool:
        batch_path = self._get_batch_path(batch_id)
        if batch_path.exists():
            batch_path.unlink()
            batches = [b for b in self.list_batches() if b["batch_id"] != batch_id]
            with open(self.index_file, "w", encoding="utf-8") as f:
                json.dump(batches, f, ensure_ascii=False, indent=2)
            return True
        return False

    def get_latest_batch(self) -> Optional[BatchHistory]:
        batches = self.list_batches()
        if not batches:
            return None
        latest = max(batches, key=lambda b: b["updated_at"])
        return self.load(latest["batch_id"])

    def find_batches_by_normalized_name(self, normalized_name: str) -> List[BatchHistory]:
        """查找归一化名称匹配的所有批次"""
        batches = self.list_batches()
        result = []
        for entry in batches:
            batch = self.load(entry["batch_id"])
            if batch and batch.normalized_name == normalized_name:
                result.append(batch)
        return result


class ProfileStorage:
    def __init__(self, work_dir: Path):
        self.work_dir = Path(work_dir)
        self.profiles_dir = self.work_dir / "profiles"
        self.profiles_dir.mkdir(parents=True, exist_ok=True)
        self.index_file = self.profiles_dir / "index.json"
        self.audit_log_file = self.profiles_dir / "audit_log.json"

    def _get_profile_path(self, profile_name: str) -> Path:
        safe_name = "".join(c for c in profile_name if c.isalnum() or c in ("-", "_", " ")).strip()
        return self.profiles_dir / f"{safe_name}.json"

    def save(self, profile: Profile) -> None:
        from datetime import datetime
        profile.updated_at = datetime.now()
        profile_path = self._get_profile_path(profile.name)
        with open(profile_path, "w", encoding="utf-8") as f:
            json.dump(profile.to_dict(), f, ensure_ascii=False, indent=2)
        self._update_index(profile)
        self._log_audit("save", profile.name, {"version": profile.version})

    def load(self, profile_name: str) -> Optional[Profile]:
        profile_path = self._get_profile_path(profile_name)
        if not profile_path.exists():
            return None
        with open(profile_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        profile = Profile.from_dict(data)
        self._log_audit("load", profile_name, {"version": profile.version})
        return profile

    def list_profiles(self) -> List[Dict]:
        if not self.index_file.exists():
            return []
        with open(self.index_file, "r", encoding="utf-8") as f:
            return json.load(f)

    def _update_index(self, profile: Profile) -> None:
        profiles = self.list_profiles()
        existing = next((p for p in profiles if p["name"] == profile.name), None)
        entry = {
            "name": profile.name,
            "description": profile.description,
            "version": profile.version,
            "created_at": profile.created_at.isoformat(),
            "updated_at": profile.updated_at.isoformat(),
            "output_format": profile.output_format,
            "conflict_strategy": profile.conflict_strategy,
        }
        if existing:
            existing.update(entry)
        else:
            profiles.append(entry)
        with open(self.index_file, "w", encoding="utf-8") as f:
            json.dump(profiles, f, ensure_ascii=False, indent=2)

    def delete(self, profile_name: str) -> bool:
        profile_path = self._get_profile_path(profile_name)
        if profile_path.exists():
            profile_path.unlink()
            profiles = [p for p in self.list_profiles() if p["name"] != profile_name]
            with open(self.index_file, "w", encoding="utf-8") as f:
                json.dump(profiles, f, ensure_ascii=False, indent=2)
            self._log_audit("delete", profile_name)
            return True
        self._log_audit("delete", profile_name, success=False, error_message="Profile not found")
        return False

    def exists(self, profile_name: str) -> bool:
        return self._get_profile_path(profile_name).exists()

    def export_profile(self, profile_name: str, export_path: Path) -> bool:
        profile = self.load(profile_name)
        if not profile:
            self._log_audit("export", profile_name, success=False, error_message="Profile not found")
            return False
        export_path = Path(export_path)
        export_path.parent.mkdir(parents=True, exist_ok=True)
        with open(export_path, "w", encoding="utf-8") as f:
            json.dump(profile.to_dict(), f, ensure_ascii=False, indent=2)
        self._log_audit("export", profile_name, {"export_path": str(export_path)})
        return True

    def import_profile(self, import_path: Path, overwrite: bool = False, rename: Optional[str] = None) -> Profile:
        import_path = Path(import_path)
        if not import_path.exists():
            raise FileNotFoundError(f"导入文件不存在: {import_path}")
        with open(import_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        profile = Profile.from_dict(data)
        if rename:
            profile.name = rename
        if self.exists(profile.name) and not overwrite:
            self._log_audit(
                "import", profile.name,
                success=False,
                error_message="Profile name already exists"
            )
            raise ProfileNameConflictError(profile.name, self.load(profile.name))
        self.save(profile)
        self._log_audit("import", profile.name, {"import_path": str(import_path)})
        return profile

    def _log_audit(self, operation: str, profile_name: Optional[str], details: Dict = None, success: bool = True, error_message: Optional[str] = None) -> None:
        import uuid as _uuid
        from datetime import datetime as _dt
        entry = AuditLogEntry(
            entry_id=str(_uuid.uuid4())[:8],
            timestamp=_dt.now(),
            operation=operation,
            profile_name=profile_name,
            details=details or {},
            success=success,
            error_message=error_message,
        )
        self._append_audit_log(entry)

    def _append_audit_log(self, entry: AuditLogEntry) -> None:
        logs = self._load_audit_log()
        logs.append(entry.to_dict())
        with open(self.audit_log_file, "w", encoding="utf-8") as f:
            json.dump(logs, f, ensure_ascii=False, indent=2)

    def _load_audit_log(self) -> List[Dict]:
        if not self.audit_log_file.exists():
            return []
        with open(self.audit_log_file, "r", encoding="utf-8") as f:
            return json.load(f)

    def get_audit_log(self, limit: int = 100) -> List[AuditLogEntry]:
        logs = self._load_audit_log()
        entries = [AuditLogEntry.from_dict(d) for d in logs]
        entries.sort(key=lambda e: e.timestamp, reverse=True)
        return entries[:limit]


class PackageStorage:
    def __init__(self, work_dir: Path):
        self.work_dir = Path(work_dir)
        self.packages_dir = self.work_dir / "packages"
        self.packages_dir.mkdir(parents=True, exist_ok=True)
        self.index_file = self.packages_dir / "index.json"

    def _get_package_path(self, package_id: str) -> Path:
        return self.packages_dir / f"{package_id}.json"

    def save(self, package: PackageRecord) -> None:
        from datetime import datetime
        package.updated_at = datetime.now()
        package_path = self._get_package_path(package.package_id)
        with open(package_path, "w", encoding="utf-8") as f:
            json.dump(package.to_dict(), f, ensure_ascii=False, indent=2)
        self._update_index(package)

    def load(self, package_id: str) -> Optional[PackageRecord]:
        package_path = self._get_package_path(package_id)
        if not package_path.exists():
            return None
        with open(package_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return PackageRecord.from_dict(data)

    def list_packages(self) -> List[Dict]:
        if not self.index_file.exists():
            return []
        with open(self.index_file, "r", encoding="utf-8") as f:
            return json.load(f)

    def _update_index(self, package: PackageRecord) -> None:
        packages = self.list_packages()
        existing = next((p for p in packages if p["package_id"] == package.package_id), None)
        entry = {
            "package_id": package.package_id,
            "batch_id": package.batch_id,
            "batch_name": package.batch_name,
            "target_dir": package.target_dir,
            "status": package.status.value,
            "created_at": package.created_at.isoformat(),
            "updated_at": package.updated_at.isoformat(),
            "started_at": package.started_at.isoformat() if package.started_at else None,
            "completed_at": package.completed_at.isoformat() if package.completed_at else None,
            "total_files": package.total_files,
            "total_size": package.total_size,
            "copied_files": package.copied_files,
            "copied_size": package.copied_size,
            "skipped_files": package.skipped_files,
            "failed_files": package.failed_files,
            "dry_run": package.dry_run,
            "notes": package.notes,
            "error_message": package.error_message,
        }
        if existing:
            existing.update(entry)
        else:
            packages.append(entry)
        with open(self.index_file, "w", encoding="utf-8") as f:
            json.dump(packages, f, ensure_ascii=False, indent=2)

    def delete(self, package_id: str) -> bool:
        package_path = self._get_package_path(package_id)
        if package_path.exists():
            package_path.unlink()
            packages = [p for p in self.list_packages() if p["package_id"] != package_id]
            with open(self.index_file, "w", encoding="utf-8") as f:
                json.dump(packages, f, ensure_ascii=False, indent=2)
            return True
        return False

    def get_latest_package(self) -> Optional[PackageRecord]:
        packages = self.list_packages()
        if not packages:
            return None
        latest = max(packages, key=lambda p: p["updated_at"])
        return self.load(latest["package_id"])

    def find_packages_by_batch(self, batch_id: str) -> List[PackageRecord]:
        packages = self.list_packages()
        result = []
        for entry in packages:
            if entry["batch_id"] == batch_id:
                pkg = self.load(entry["package_id"])
                if pkg:
                    result.append(pkg)
        result.sort(key=lambda p: p.created_at, reverse=True)
        return result

    def find_packages_by_status(self, status: str) -> List[PackageRecord]:
        packages = self.list_packages()
        result = []
        for entry in packages:
            if entry["status"] == status:
                pkg = self.load(entry["package_id"])
                if pkg:
                    result.append(pkg)
        result.sort(key=lambda p: p.created_at, reverse=True)
        return result


class AcceptanceAuditStorage:
    def __init__(self, work_dir: Path):
        self.work_dir = Path(work_dir)
        self.audits_dir = self.work_dir / "acceptance_audits"
        self.audits_dir.mkdir(parents=True, exist_ok=True)
        self.index_file = self.audits_dir / "index.json"

    def _get_audit_path(self, audit_id: str) -> Path:
        return self.audits_dir / f"{audit_id}.json"

    def save(self, record: "AcceptanceAuditRecord") -> None:
        from datetime import datetime
        from .models import AcceptanceAuditRecord
        record.updated_at = datetime.now()
        audit_path = self._get_audit_path(record.audit_id)
        with open(audit_path, "w", encoding="utf-8") as f:
            json.dump(record.to_dict(), f, ensure_ascii=False, indent=2)
        self._update_index(record)

    def load(self, audit_id: str) -> Optional["AcceptanceAuditRecord"]:
        from .models import AcceptanceAuditRecord
        audit_path = self._get_audit_path(audit_id)
        if not audit_path.exists():
            return None
        with open(audit_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return AcceptanceAuditRecord.from_dict(data)

    def list_audits(self) -> List[Dict]:
        if not self.index_file.exists():
            return []
        with open(self.index_file, "r", encoding="utf-8") as f:
            return json.load(f)

    def _update_index(self, record: "AcceptanceAuditRecord") -> None:
        audits = self.list_audits()
        existing = next((a for a in audits if a["audit_id"] == record.audit_id), None)
        entry = {
            "audit_id": record.audit_id,
            "client_name": record.client_name,
            "batch_name": record.batch_name,
            "source_dir": record.source_dir,
            "started_at": record.started_at.isoformat(),
            "completed_at": record.completed_at.isoformat() if record.completed_at else None,
            "status": record.status.value,
            "total_rules": len(record.results),
            "passed": sum(1 for r in record.results if r.status.value == "pass"),
            "failed": sum(1 for r in record.results if r.status.value == "fail"),
            "warnings": sum(1 for r in record.results if r.status.value == "warning"),
            "exported_paths": record.exported_paths,
            "error_message": record.error_message,
        }
        if existing:
            existing.update(entry)
        else:
            audits.append(entry)
        with open(self.index_file, "w", encoding="utf-8") as f:
            json.dump(audits, f, ensure_ascii=False, indent=2)

    def delete(self, audit_id: str) -> bool:
        audit_path = self._get_audit_path(audit_id)
        if audit_path.exists():
            audit_path.unlink()
            audits = [a for a in self.list_audits() if a["audit_id"] != audit_id]
            with open(self.index_file, "w", encoding="utf-8") as f:
                json.dump(audits, f, ensure_ascii=False, indent=2)
            return True
        return False

    def get_latest_audit(self) -> Optional["AcceptanceAuditRecord"]:
        audits = self.list_audits()
        if not audits:
            return None
        latest = max(audits, key=lambda a: a["started_at"])
        return self.load(latest["audit_id"])

    def find_audits_by_batch(self, batch_name: str) -> List["AcceptanceAuditRecord"]:
        audits = self.list_audits()
        result = []
        for entry in audits:
            if entry["batch_name"] == batch_name:
                audit = self.load(entry["audit_id"])
                if audit:
                    result.append(audit)
        result.sort(key=lambda a: a.started_at, reverse=True)
        return result

    def find_audits_by_client(self, client_name: str) -> List["AcceptanceAuditRecord"]:
        audits = self.list_audits()
        result = []
        for entry in audits:
            if entry["client_name"] == client_name:
                audit = self.load(entry["audit_id"])
                if audit:
                    result.append(audit)
        result.sort(key=lambda a: a.started_at, reverse=True)
        return result

    def find_recent_audits(self, limit: int = 10) -> List["AcceptanceAuditRecord"]:
        audits = self.list_audits()
        audits.sort(key=lambda a: a["started_at"], reverse=True)
        result = []
        for entry in audits[:limit]:
            audit = self.load(entry["audit_id"])
            if audit:
                result.append(audit)
        return result
