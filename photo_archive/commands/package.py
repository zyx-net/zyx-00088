from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional
import hashlib
import json
import shutil
import uuid

from ..config import Config
from ..models import (
    BatchHistory,
    CorrectionStatus,
    FileStatus,
    PackageFileItem,
    PackageInsufficientSpaceError,
    PackageRecord,
    PackageSourceModifiedError,
    PackageStatus,
    PackageTargetExistsError,
    PackageFileConflictError,
)
from ..scanner import FileScanner
from ..storage import BatchStorage, PackageStorage
from .utils import get_or_create_batch


DELIVERY_STRUCTURE = {
    "photos": "原始照片",
    "thumbnails": "缩略图",
    "selected": "精选照片",
    "videos": "视频文件",
}


def _get_disk_usage(directory: Path) -> tuple:
    """获取磁盘使用情况"""
    try:
        usage = shutil.disk_usage(str(directory))
        return usage.total, usage.used, usage.free
    except Exception:
        return 0, 0, 0


def _collect_eligible_files(
    batch: BatchHistory,
    include_filenames: Optional[List[str]] = None,
    exclude_filenames: Optional[List[str]] = None,
    include_cameras: Optional[List[str]] = None,
) -> List[PackageFileItem]:
    """收集符合条件的文件，只选择已完成归档且状态正常的文件"""
    items: List[PackageFileItem] = []
    excluded = set(exclude_filenames) if exclude_filenames else set()
    included = set(include_filenames) if include_filenames else None
    cameras = set(include_cameras) if include_cameras else None

    for correction in batch.corrections:
        if correction.status != CorrectionStatus.COMPLETED or correction.rolled_back:
            continue

        target_name = Path(correction.target).name
        source_path = correction.source

        if not Path(source_path).exists():
            continue

        if target_name in excluded:
            continue

        if included and target_name not in included:
            continue

        scanned_file = batch.scanned_files.get(source_path)
        if not scanned_file:
            continue

        delivery_item = batch.delivery_list.get(target_name)
        if delivery_item and delivery_item.status != FileStatus.OK:
            continue

        camera = scanned_file.camera
        if cameras and camera and camera not in cameras:
            continue

        package_item = PackageFileItem(
            source_path=source_path,
            target_path="",
            file_name=target_name,
            size=scanned_file.size,
            hash=correction.source_hash or scanned_file.hash,
            camera=camera,
            status=FileStatus.OK,
            copied=False,
        )
        items.append(package_item)

    items.sort(key=lambda x: x.file_name)
    return items


def _build_target_structure(
    items: List[PackageFileItem],
    target_dir: Path,
) -> Dict:
    """构建目标目录结构，为每个文件分配目标路径"""
    structure = {
        "photos": [],
        "thumbnails": [],
        "selected": [],
        "videos": [],
    }
    structure_description = {}

    photo_extensions = {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".raw", ".cr2", ".nef", ".arw", ".dng"}
    video_extensions = {".mp4", ".mov", ".avi", ".mkv", ".mts"}

    for item in items:
        ext = Path(item.file_name).suffix.lower()

        if ext in video_extensions:
            target_subdir = "videos"
        elif ext in photo_extensions:
            target_subdir = "photos"
        else:
            target_subdir = "photos"

        target_path = target_dir / target_subdir / item.file_name
        item.target_path = str(target_path)
        structure[target_subdir].append(item)

    for key, value in DELIVERY_STRUCTURE.items():
        count = len(structure[key])
        if count > 0:
            structure_description[key] = {
                "description": value,
                "count": count,
                "size": sum(item.size for item in structure[key]),
            }

    return structure_description


def _check_target_directory(target_dir: Path, force: bool) -> None:
    """检查目标目录是否已存在"""
    if target_dir.exists():
        if not force:
            raise PackageTargetExistsError(str(target_dir))


def _check_disk_space(target_dir: Path, required_size: int) -> None:
    """检查磁盘空间是否足够"""
    parent_dir = target_dir.parent
    if not parent_dir.exists():
        parent_dir.mkdir(parents=True, exist_ok=True)

    _, _, free = _get_disk_usage(parent_dir)
    buffer = required_size * 0.1
    total_required = required_size + buffer

    if free > 0 and free < total_required:
        raise PackageInsufficientSpaceError(
            required=int(total_required),
            available=free,
            target_dir=str(target_dir),
        )


def _check_source_file_integrity(
    item: PackageFileItem,
    scanner: FileScanner,
) -> None:
    """检查源文件是否被篡改"""
    source_path = Path(item.source_path)
    if not source_path.exists():
        raise FileNotFoundError(f"源文件不存在: {item.source_path}")

    current_hash = scanner.calculate_hash(source_path)
    if current_hash != item.hash:
        raise PackageSourceModifiedError(
            source_path=item.source_path,
            expected_hash=item.hash,
            actual_hash=current_hash,
        )


def _check_target_file_conflict(
    item: PackageFileItem,
    scanner: FileScanner,
    skip_conflicts: bool,
) -> Optional[PackageFileItem]:
    """检查目标文件是否存在冲突"""
    target_path = Path(item.target_path)
    if not target_path.exists():
        return None

    current_hash = scanner.calculate_hash(target_path)
    if current_hash == item.hash:
        item.copied = True
        skipped_item = PackageFileItem(
            source_path=item.source_path,
            target_path=item.target_path,
            file_name=item.file_name,
            size=item.size,
            hash=item.hash,
            camera=item.camera,
            status=FileStatus.OK,
            copied=True,
            skip_reason="目标文件已存在且内容相同",
        )
        return skipped_item

    if skip_conflicts:
        skipped_item = PackageFileItem(
            source_path=item.source_path,
            target_path=item.target_path,
            file_name=item.file_name,
            size=item.size,
            hash=item.hash,
            camera=item.camera,
            status=FileStatus.OK,
            copied=False,
            skip_reason="目标文件已存在且内容不匹配，已跳过",
        )
        return skipped_item

    raise PackageFileConflictError(
        file_name=item.file_name,
        target_path=item.target_path,
    )


def _copy_file(item: PackageFileItem) -> None:
    """复制文件到目标位置"""
    source_path = Path(item.source_path)
    target_path = Path(item.target_path)
    target_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source_path, target_path)
    item.copied = True


def _generate_manifest(
    package: PackageRecord,
    target_dir: Path,
    structure_description: Dict,
) -> Path:
    """生成 manifest.json 文件"""
    manifest_path = target_dir / "manifest.json"

    manifest = {
        "package_id": package.package_id,
        "batch_id": package.batch_id,
        "batch_name": package.batch_name,
        "generated_at": datetime.now().isoformat(),
        "total_files": package.total_files,
        "total_size": package.total_size,
        "package_structure": structure_description,
        "input_batches": package.input_batches,
        "notes": package.notes,
        "files": [
            {
                "file_name": item.file_name,
                "size": item.size,
                "hash": item.hash,
                "hash_algorithm": "sha256",
                "camera": item.camera,
                "source_path": item.source_path,
                "target_path": item.target_path,
                "copied": item.copied,
            }
            for item in package.items
        ],
        "skipped_files": [
            {
                "file_name": item.file_name,
                "size": item.size,
                "hash": item.hash,
                "reason": item.skip_reason,
            }
            for item in package.skipped_items
        ],
    }

    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)

    return manifest_path


def _generate_checksum(
    package: PackageRecord,
    target_dir: Path,
) -> Path:
    """生成校验摘要文件"""
    checksum_path = target_dir / "checksums.sha256"

    lines = []
    for item in package.items:
        if item.copied:
            relative_path = Path(item.target_path).relative_to(target_dir)
            lines.append(f"{item.hash}  {relative_path}")

    lines.sort()
    content = "\n".join(lines) + "\n"

    with open(checksum_path, "w", encoding="utf-8") as f:
        f.write(content)

    return checksum_path


def _generate_readme(
    package: PackageRecord,
    target_dir: Path,
    structure_description: Dict,
) -> Path:
    """生成可读的交付说明文件"""
    readme_path = target_dir / "交付说明.txt"

    lines = []
    lines.append("=" * 60)
    lines.append("照片交付说明")
    lines.append("=" * 60)
    lines.append("")
    lines.append(f"批次名称: {package.batch_name}")
    lines.append(f"批次ID: {package.batch_id}")
    lines.append(f"打包ID: {package.package_id}")
    lines.append(f"生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append("")
    lines.append(f"总计文件: {package.total_files} 个")
    lines.append(f"总计大小: {_format_size(package.total_size)}")
    lines.append("")

    if package.notes:
        lines.append("备注:")
        lines.append(f"  {package.notes}")
        lines.append("")

    lines.append("目录结构:")
    for key, info in structure_description.items():
        lines.append(f"  {key}/ ({info['description']})")
        lines.append(f"    文件数量: {info['count']} 个")
        lines.append(f"    总大小: {_format_size(info['size'])}")
    lines.append("")

    lines.append("校验文件:")
    lines.append("  - manifest.json: 详细的文件清单和元数据")
    lines.append("  - checksums.sha256: SHA256 校验和，可用于验证文件完整性")
    lines.append("")

    lines.append("校验方法:")
    lines.append("  Windows (PowerShell):")
    lines.append(f"    Get-FileHash -Algorithm SHA256 {Path(package.target_dir).name}/photos/*.jpg | Format-Table")
    lines.append("")
    lines.append("  macOS/Linux:")
    lines.append(f"    cd {Path(package.target_dir).name} && sha256sum -c checksums.sha256")
    lines.append("")

    if package.skipped_items:
        lines.append(f"跳过的文件 ({len(package.skipped_items)} 个):")
        for item in package.skipped_items:
            lines.append(f"  - {item.file_name}: {item.skip_reason}")
        lines.append("")

    if package.failed_items:
        lines.append(f"失败的文件 ({len(package.failed_items)} 个):")
        for fail in package.failed_items:
            lines.append(f"  - {fail.get('file_name', '未知文件')}: {fail.get('error', '未知错误')}")
        lines.append("")

    lines.append("=" * 60)
    lines.append("本交付包由 photo-archive 工具自动生成")
    lines.append("=" * 60)

    content = "\n".join(lines)

    with open(readme_path, "w", encoding="utf-8") as f:
        f.write(content)

    return readme_path


def _format_size(size_bytes: int) -> str:
    """格式化文件大小为可读字符串"""
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    elif size_bytes < 1024 * 1024 * 1024:
        return f"{size_bytes / (1024 * 1024):.1f} MB"
    else:
        return f"{size_bytes / (1024 * 1024 * 1024):.1f} GB"


def create_delivery_package(
    config: Config,
    storage: BatchStorage,
    package_storage: PackageStorage,
    target_dir: str,
    batch_id: Optional[str] = None,
    batch_name: Optional[str] = None,
    notes: str = "",
    include_filenames: Optional[List[str]] = None,
    exclude_filenames: Optional[List[str]] = None,
    include_cameras: Optional[List[str]] = None,
    dry_run: bool = False,
    force: bool = False,
    skip_conflicts: bool = False,
) -> Dict:
    """创建交付包"""
    target_path = Path(target_dir).resolve()

    batch, _ = get_or_create_batch(storage, batch_id, batch_name)

    if not batch.corrections:
        raise ValueError("批次没有修正记录，请先执行校验和归档流程")

    completed_count = len([
        c for c in batch.corrections
        if c.status == CorrectionStatus.COMPLETED and not c.rolled_back
    ])
    if completed_count == 0:
        raise ValueError("批次没有已完成的归档文件，请先执行 apply 完成归档")

    items = _collect_eligible_files(
        batch,
        include_filenames,
        exclude_filenames,
        include_cameras,
    )

    if not items:
        raise ValueError("没有符合条件的文件可打包")

    total_size = sum(item.size for item in items)
    total_files = len(items)

    package_id = str(uuid.uuid4())[:8]
    package = PackageRecord(
        package_id=package_id,
        batch_id=batch.batch_id,
        batch_name=batch.name,
        target_dir=str(target_path),
        status=PackageStatus.PENDING,
        total_files=total_files,
        total_size=total_size,
        dry_run=dry_run,
        items=items,
        input_batches=[batch.batch_id],
        notes=notes,
    )

    if dry_run:
        package.status = PackageStatus.DRY_RUN
        structure_description = _build_target_structure(items, target_path)
        package.package_structure = structure_description
        package_storage.save(package)
        return package.to_dict()

    _check_target_directory(target_path, force)
    _check_disk_space(target_path, total_size)

    structure_description = _build_target_structure(items, target_path)
    package.package_structure = structure_description

    scanner = FileScanner(config)

    package.status = PackageStatus.IN_PROGRESS
    package.started_at = datetime.now()
    package_storage.save(package)

    copied_items: List[PackageFileItem] = []
    skipped_items: List[PackageFileItem] = []
    failed_items: List[Dict] = []

    try:
        for item in items:
            try:
                _check_source_file_integrity(item, scanner)

                conflict = _check_target_file_conflict(item, scanner, skip_conflicts)
                if conflict:
                    skipped_items.append(conflict)
                    if not conflict.copied:
                        continue

                _copy_file(item)
                copied_items.append(item)

            except PackageFileConflictError:
                raise
            except PackageSourceModifiedError:
                raise
            except Exception as e:
                failed_items.append({
                    "file_name": item.file_name,
                    "source_path": item.source_path,
                    "target_path": item.target_path,
                    "error": str(e),
                })

        package.items = copied_items
        package.skipped_items = skipped_items
        package.failed_items = failed_items
        package.copied_files = len(copied_items)
        package.copied_size = sum(item.size for item in copied_items)
        package.skipped_files = len(skipped_items)
        package.failed_files = len(failed_items)

        if not skip_conflicts and failed_items:
            package.status = PackageStatus.FAILED
            package.error_message = f"打包失败，{len(failed_items)} 个文件出错"
            package.completed_at = datetime.now()
            package_storage.save(package)
            return package.to_dict()

        target_path.mkdir(parents=True, exist_ok=True)

        manifest_path = _generate_manifest(package, target_path, structure_description)
        checksum_path = _generate_checksum(package, target_path)
        readme_path = _generate_readme(package, target_path, structure_description)

        package.manifest_path = str(manifest_path)
        package.checksum_path = str(checksum_path)
        package.readme_path = str(readme_path)

        if failed_items:
            package.status = PackageStatus.FAILED
            package.error_message = f"打包部分完成，{len(failed_items)} 个文件出错"
        else:
            package.status = PackageStatus.COMPLETED

        package.completed_at = datetime.now()
        package_storage.save(package)

        return package.to_dict()

    except Exception as e:
        package.status = PackageStatus.FAILED
        package.error_message = str(e)
        package.completed_at = datetime.now()
        package.failed_items = failed_items
        package.failed_files = len(failed_items)
        package_storage.save(package)
        raise


def list_packages(
    package_storage: PackageStorage,
    batch_id: Optional[str] = None,
    status: Optional[str] = None,
    limit: int = 20,
) -> Dict:
    """查询打包历史记录"""
    if batch_id:
        packages = package_storage.find_packages_by_batch(batch_id)
    elif status:
        packages = package_storage.find_packages_by_status(status)
    else:
        all_packages = package_storage.list_packages()
        all_packages.sort(key=lambda p: p["updated_at"], reverse=True)
        packages = []
        for entry in all_packages[:limit]:
            pkg = package_storage.load(entry["package_id"])
            if pkg:
                packages.append(pkg)

    packages.sort(key=lambda p: p.created_at, reverse=True)
    packages = packages[:limit]

    return {
        "count": len(packages),
        "packages": [p.to_dict() for p in packages],
    }


def show_package(
    package_storage: PackageStorage,
    package_id: str,
) -> Dict:
    """查看打包详情"""
    package = package_storage.load(package_id)
    if not package:
        raise ValueError(f"找不到打包记录: {package_id}")

    return package.to_dict()
