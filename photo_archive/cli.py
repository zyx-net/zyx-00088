from pathlib import Path
from typing import Optional
import json
import sys

import click

from .config import Config
from .storage import BatchStorage, ProfileStorage, PackageStorage, AcceptanceAuditStorage
from .models import (
    BatchNameConflictError,
    ProfileNameConflictError,
    Profile,
    PackageTargetExistsError,
    PackageFileConflictError,
    PackageInsufficientSpaceError,
    PackageSourceModifiedError,
    AcceptanceConfigError,
    AcceptanceRuleConflictError,
    AcceptanceDirectoryNotFoundError,
    AcceptanceExportExistsError,
    AcceptanceWritePermissionError,
)
from .commands.init_sample import generate_sample_data
from .commands.scan import scan_directory
from .commands.import_list import import_delivery_list
from .commands.verify import verify_batch
from .commands.plan import generate_correction_plan
from .commands.apply import apply_corrections, apply_single_correction
from .commands.undo import undo_corrections, has_undoable_operations
from .commands.report import generate_report
from .commands.status import get_batch_status
from .commands.utils import get_or_create_batch
from .commands.package import (
    create_delivery_package,
    list_packages,
    show_package,
)
from .commands.profile import (
    save_profile,
    load_profile,
    list_profiles,
    delete_profile,
    export_profile,
    import_profile,
    get_audit_log,
    apply_profile_to_config,
    merge_profile_with_cli_args,
)
from .commands.acceptance_audit import (
    load_acceptance_config,
    run_acceptance_audit,
    list_audit_history,
    get_audit_detail,
    reexport_audit_result,
    generate_text_summary,
)


def _get_config(config_path: str) -> Config:
    return Config.load(config_path)


def _get_storage(config: Config) -> BatchStorage:
    return BatchStorage(config.work_dir)


def _get_profile_storage(config: Config) -> ProfileStorage:
    return ProfileStorage(config.work_dir)


def _get_package_storage(config: Config) -> PackageStorage:
    return PackageStorage(config.work_dir)


def _get_acceptance_storage(config: Config) -> AcceptanceAuditStorage:
    return AcceptanceAuditStorage(config.work_dir)


def _print_json(data) -> None:
    click.echo(json.dumps(data, ensure_ascii=False, indent=2))


def _load_and_apply_profile(ctx, profile_name: Optional[str]) -> Optional[Profile]:
    if not profile_name:
        return None
    profile_storage = ctx.obj["profile_storage"]
    profile = profile_storage.load(profile_name)
    if not profile:
        raise click.BadParameter(f"找不到 Profile: {profile_name}", param_hint="--profile")
    config = ctx.obj["config"]
    apply_profile_to_config(profile, config)
    ctx.obj["config"] = config
    return profile


def _get_effective_args(profile: Optional[Profile], **cli_args) -> dict:
    return merge_profile_with_cli_args(profile, cli_args)


EXIT_CODES = {
    0: "成功",
    1: "通用错误（命令执行失败）",
    2: "清单错误（损坏或包含重复文件名）",
    3: "哈希不一致（扫描后文件被篡改或期望哈希不匹配）",
    4: "重复文件名（源目录或清单中存在重复）",
    5: "缺片（清单中存在但源目录中缺失的文件）",
    6: "没有可撤销的操作",
    7: "合并冲突（多清单导入存在冲突，需要人工解决）",
    8: "批次名冲突（归一化后与现有批次冲突，需要人工处理）",
    9: "执行冲突（目标文件被外部修改或内容不匹配）",
    10: "Profile 名称冲突",
    11: "打包目标目录已存在",
    12: "打包文件冲突（目标目录存在同名文件且内容不匹配）",
    13: "磁盘空间不足",
    14: "源文件被篡改（打包时检测到哈希不一致）",
    15: "验收配置错误（缺少必填字段）",
    16: "验收规则冲突（同一类型规则启用多个）",
    17: "待检查目录不存在",
    18: "导出文件已存在",
    19: "只读目录写入失败",
    20: "验收审计失败（存在未通过的检查项）",
}


@click.group()
@click.option("--config", "-c", default="./config.yaml", help="配置文件路径")
@click.pass_context
def cli(ctx, config: str):
    """多命令离线相册归档校验 CLI 工具

    非零退出码说明:
      1 - 通用错误
      2 - 清单错误
      3 - 哈希不一致
      4 - 存在重复文件名
      5 - 存在缺片
      6 - 没有可撤销的操作
      7 - 合并冲突
      8 - 批次名冲突
      10 - Profile 名称冲突
      11 - 打包目标目录已存在
      12 - 打包文件冲突
      13 - 磁盘空间不足
      14 - 源文件被篡改
      15 - 验收配置错误
      16 - 验收规则冲突
      17 - 待检查目录不存在
      18 - 导出文件已存在
      19 - 只读目录写入失败
      20 - 验收审计失败
    """
    ctx.ensure_object(dict)
    cfg = _get_config(config)
    ctx.obj["config"] = cfg
    ctx.obj["storage"] = _get_storage(cfg)
    ctx.obj["profile_storage"] = _get_profile_storage(cfg)
    ctx.obj["package_storage"] = _get_package_storage(cfg)
    ctx.obj["acceptance_storage"] = _get_acceptance_storage(cfg)


@cli.command("init-sample")
@click.argument("sample_dir", default="./sample_data")
@click.option("--cameras", default=3, type=int, help="机位数量")
@click.option("--photos", default=5, type=int, help="每个机位的照片数量")
@click.option("--batch", "batch_name", default="WEDDING001", help="批次名称")
def init_sample(sample_dir: str, cameras: int, photos: int, batch_name: str):
    """初始化样例数据"""
    result = generate_sample_data(sample_dir, cameras, photos, batch_name)
    click.echo(f"[OK] 样例数据已生成到: {sample_dir}")
    click.echo(f"  源目录: {result['source_dir']}")
    click.echo(f"  交付清单: {result['delivery_manifest']}")
    click.echo(f"  配置文件: {result['config']}")
    click.echo(f"  源文件: {sum(len(f) for f in result['source_files'].values())} 个")
    click.echo(f"  期望交付: {len(result['expected_files'])} 个")
    sys.exit(0)


@cli.command("scan")
@click.argument("source_dir")
@click.option("--batch-id", help="批次ID，不指定则使用最新批次或创建新批次")
@click.option("--batch-name", help="新批次名称")
@click.option("--recursive/--no-recursive", default=True, help="是否递归扫描子目录")
@click.option("--json", "output_json", is_flag=True, help="输出JSON格式")
@click.pass_context
def scan(ctx, source_dir: str, batch_id: Optional[str], batch_name: Optional[str], recursive: bool, output_json: bool):
    """扫描源目录中的照片文件"""
    config = ctx.obj["config"]
    storage = ctx.obj["storage"]

    try:
        result = scan_directory(config, storage, source_dir, batch_id, batch_name, recursive)
        if output_json:
            _print_json(result)
        else:
            click.echo(f"[OK] 扫描完成")
            click.echo(f"  批次: {result['batch_name']} ({result['batch_id']})")
            click.echo(f"  扫描目录: {result['source_dir']}")
            click.echo(f"  发现文件: {result['scanned_count']} 个")
        sys.exit(0)
    except BatchNameConflictError as e:
        if output_json:
            _print_json(e.to_dict())
        else:
            click.echo(f"[FAIL] 批次名冲突: {e}", err=True)
            click.echo("\n现有冲突批次:", err=True)
            for b in e.conflicting_batches:
                click.echo(f"  - {b.batch_id}: {b.name}", err=True)
            click.echo(f"\n归一化后名称: {e.normalized_name}", err=True)
        sys.exit(8)
    except Exception as e:
        click.echo(f"[FAIL] 扫描失败: {e}", err=True)
        sys.exit(1)


@cli.command("import-list")
@click.argument("manifest_path")
@click.option("--batch-id", help="批次ID")
@click.option("--batch-name", help="新批次名称")
@click.option("--json", "output_json", is_flag=True, help="输出JSON格式")
@click.option("--merge/--no-merge", default=True, help="是否合并到现有清单")
@click.option("--dry-run", is_flag=True, help="预览模式，只输出将新增、跳过、冲突的数量和明细，不修改批次历史")
@click.option("--allow-overwrite", is_flag=True, help="允许覆盖已有清单项（默认保护旧数据，遇到不同的同名项将被跳过）")
@click.pass_context
def import_list(ctx, manifest_path: str, batch_id: Optional[str], batch_name: Optional[str], output_json: bool, merge: bool, dry_run: bool, allow_overwrite: bool):
    """导入交付清单 (CSV 格式)"""
    config = ctx.obj["config"]
    storage = ctx.obj["storage"]

    try:
        result = import_delivery_list(
            config, storage, manifest_path, batch_id, batch_name, merge,
            dry_run, allow_overwrite
        )
        conflict_count = result.get("conflict_count", 0)

        if output_json:
            _print_json(result)
        else:
            if dry_run:
                click.echo(f"[PREVIEW] 导入预览（未写入批次历史）")
            else:
                if conflict_count > 0:
                    click.echo(f"[WARN] 导入完成但存在冲突")
                else:
                    click.echo(f"[OK] 导入完成")

            click.echo(f"  批次: {result['batch_name']} ({result['batch_id']})")
            click.echo(f"  清单文件: {result['source_file']}")
            click.echo(f"  导入条目: {result['imported_count']} 个")
            click.echo(f"  新增: {result['added_count']} 个")
            if allow_overwrite:
                click.echo(f"  覆盖: {result['overwritten_count']} 个")
            click.echo(f"  跳过: {result['skipped_count']} 个")
            click.echo(f"  冲突: {result['conflicted_count']} 个")

            if result['added_items']:
                click.echo("\n新增明细:")
                for name in result['added_items']:
                    click.echo(f"  + {name}")

            if result['overwritten_items']:
                click.echo("\n覆盖明细:")
                for name in result['overwritten_items']:
                    click.echo(f"  ~ {name}")

            if result['skipped_items']:
                click.echo("\n跳过明细:")
                for name in result['skipped_items']:
                    click.echo(f"  - {name}")

            if result['conflicted_items']:
                click.echo("\n冲突明细:")
                for name in result['conflicted_items']:
                    click.echo(f"  ! {name}")

            if conflict_count > 0:
                click.echo("\n冲突列表:")
                for c in result.get("conflicts", []):
                    click.echo(f"  - [{c['conflict_type']}] {c['message']}")

            if dry_run:
                click.echo("\n提示: 添加 --allow-overwrite 可允许覆盖已有的不同名清单项")

        if conflict_count > 0:
            sys.exit(7)
        sys.exit(0)
    except BatchNameConflictError as e:
        if output_json:
            _print_json(e.to_dict())
        else:
            click.echo(f"[FAIL] 批次名冲突: {e}", err=True)
            click.echo("\n现有冲突批次:", err=True)
            for b in e.conflicting_batches:
                click.echo(f"  - {b.batch_id}: {b.name}", err=True)
            click.echo(f"\n归一化后名称: {e.normalized_name}", err=True)
        sys.exit(8)
    except ValueError as e:
        if "重复文件名" in str(e) or "损坏" in str(e):
            click.echo(f"[FAIL] 清单错误: {e}", err=True)
            sys.exit(2)
        click.echo(f"[FAIL] 导入失败: {e}", err=True)
        sys.exit(1)
    except Exception as e:
        click.echo(f"[FAIL] 导入失败: {e}", err=True)
        sys.exit(1)


@cli.command("verify")
@click.option("--batch-id", help="批次ID")
@click.option("--check-hashes/--no-check-hashes", default=True, help="是否校验哈希")
@click.option("--json", "output_json", is_flag=True, help="输出JSON格式")
@click.pass_context
def verify(ctx, batch_id: Optional[str], check_hashes: bool, output_json: bool):
    """校验交付清单与源文件的匹配情况"""
    config = ctx.obj["config"]
    storage = ctx.obj["storage"]

    try:
        result = verify_batch(config, storage, batch_id, check_hashes)
        if output_json:
            _print_json(result)
        else:
            click.echo(f"[OK] 校验完成")
            click.echo(f"  批次: {result['batch_name']} ({result['batch_id']})")
            click.echo(f"  交付清单: {result['total_delivery']} 项")
            click.echo(f"  扫描文件: {result['total_scanned']} 个")
            click.echo(f"  匹配正常: {result['ok_count']} 个")
            click.echo(f"  缺片: {result['missing_count']} 个")
            click.echo(f"  重复: {result['duplicate_count']} 个")
            click.echo(f"  哈希不一致: {result['hash_mismatch_count']} 个")

            if result["missing"]:
                click.echo("\n缺片列表:")
                for name in result["missing"]:
                    click.echo(f"  - {name}")

            if result["duplicates"]:
                click.echo("\n重复文件:")
                for dup in result["duplicates"]:
                    click.echo(f"  - {dup['target_name']} ({dup['count']} 个源文件)")

            if result["hash_mismatches"]:
                click.echo("\n哈希不一致:")
                for hm in result["hash_mismatches"]:
                    click.echo(f"  - {hm['target_name']}")

        if result["hash_mismatch_count"] > 0:
            sys.exit(3)
        if result["duplicate_count"] > 0:
            sys.exit(4)
        if result["missing_count"] > 0:
            sys.exit(5)
        sys.exit(0)
    except Exception as e:
        click.echo(f"[FAIL] 校验失败: {e}", err=True)
        sys.exit(1)


@cli.command("plan")
@click.option("--batch-id", help="批次ID")
@click.option("--save", "save_as", help="保存为命名批次快照，可用于后续断点续执行")
@click.option("--description", help="批次快照描述")
@click.option("--profile", help="使用保存的 Profile 配置")
@click.option("--json/--no-json", "output_json", default=None, help="输出JSON格式")
@click.pass_context
def plan(ctx, batch_id: Optional[str], save_as: Optional[str], description: Optional[str], profile: Optional[str], output_json: Optional[bool]):
    """生成修正计划，支持保存为命名批次快照"""
    profile_obj = _load_and_apply_profile(ctx, profile)
    effective_args = _get_effective_args(profile_obj, output_json=output_json)
    effective_output_json = effective_args["output_json"]

    config = ctx.obj["config"]
    storage = ctx.obj["storage"]

    try:
        batch, _ = get_or_create_batch(storage, batch_id)
        unresolved_conflicts = [c for c in batch.conflicts if not c.resolved]
        if unresolved_conflicts:
            click.echo(f"[FAIL] 存在 {len(unresolved_conflicts)} 个未解决的冲突，请先解决冲突后再执行此操作", err=True)
            click.echo("冲突列表:", err=True)
            for c in unresolved_conflicts:
                click.echo(f"  - [{c.conflict_type.value}] {c.message}", err=True)
            sys.exit(7)

        result = generate_correction_plan(config, storage, batch_id, save_as, description)
        if effective_output_json:
            _print_json(result)
        else:
            click.echo(f"[OK] 修正计划已生成")
            if profile:
                click.echo(f"  使用 Profile: {profile}")
            click.echo(f"  批次: {result['batch_name']} ({result['batch_id']})")
            click.echo(f"  归档目录: {result['archive_dir']}")
            click.echo(f"  总修正项: {result['correction_count']} 个")
            stats = result.get("statistics", {})
            click.echo(f"  待执行: {stats.get('pending', 0)} 个")
            click.echo(f"  已完成: {stats.get('completed', 0)} 个")
            click.echo(f"  冲突: {stats.get('conflicted', 0)} 个")
            click.echo(f"  失败: {stats.get('failed', 0)} 个")
            click.echo(f"  跳过: {stats.get('skipped', 0)} 个")
            click.echo(f"  已撤销: {stats.get('rolled_back', 0)} 个")
            click.echo(f"  可撤销: {stats.get('undoable', 0)} 个")

            if result.get("snapshot_id"):
                click.echo(f"\n[OK] 已保存批次快照: {result['snapshot_name']} ({result['snapshot_id']})")
                click.echo(f"  包含待执行项: {result['snapshot_correction_count']} 个")
                click.echo(f"  继续执行: photo-archive -c config.yaml apply --from-snapshot {result['snapshot_id']}")

            click.echo("\n修正明细:")
            for i, corr in enumerate(result["corrections"], 1):
                status = ""
                if corr.get("status") == "completed":
                    status = " [已完成]"
                elif corr.get("status") == "pending":
                    status = " [待执行]"
                elif corr.get("status") == "conflicted":
                    status = " [冲突]"
                elif corr.get("status") == "failed":
                    status = " [失败]"
                elif corr.get("status") == "skipped":
                    status = " [跳过]"
                elif corr.get("status") == "rolled_back":
                    status = " [已撤销]"
                click.echo(f"  {i}. [{corr['type']}] {corr['reason']}{status}")
                if corr["source"]:
                    click.echo(f"     源: {corr['source']}")
                click.echo(f"     目标: {corr['target']}")
                if corr.get("failure_reason"):
                    click.echo(f"     失败原因: {corr['failure_reason']}")
        sys.exit(0)
    except SystemExit:
        raise
    except Exception as e:
        click.echo(f"[FAIL] 生成计划失败: {e}", err=True)
        sys.exit(1)


@cli.command("apply")
@click.option("--batch-id", help="批次ID")
@click.option("--correction-id", help="指定要应用的单个修正ID")
@click.option("--limit", type=int, help="限制应用的修正数量")
@click.option("--from-snapshot", help="从指定的快照继续执行")
@click.option("--resume/--no-resume", default=None, help="是否跳过已完成的项继续执行（默认跳过）")
@click.option("--skip-conflicts", is_flag=True, help="跳过存在冲突的项，继续执行其他项")
@click.option("--profile", help="使用保存的 Profile 配置")
@click.option("--json/--no-json", "output_json", default=None, help="输出JSON格式")
@click.pass_context
def apply(ctx, batch_id: Optional[str], correction_id: Optional[str], limit: Optional[int], from_snapshot: Optional[str], resume: Optional[bool], skip_conflicts: bool, profile: Optional[str], output_json: Optional[bool]):
    """应用修正计划，支持从快照断点续执行"""
    profile_obj = _load_and_apply_profile(ctx, profile)
    effective_args = _get_effective_args(
        profile_obj,
        resume=resume,
        skip_conflicts=skip_conflicts,
        limit=limit,
        output_json=output_json
    )
    effective_resume = effective_args["resume"]
    effective_skip_conflicts = effective_args["skip_conflicts"]
    effective_limit = effective_args["limit"]
    effective_output_json = effective_args["output_json"]

    config = ctx.obj["config"]
    storage = ctx.obj["storage"]

    try:
        batch, _ = get_or_create_batch(storage, batch_id)
        unresolved_conflicts = [c for c in batch.conflicts if not c.resolved]
        if unresolved_conflicts and not effective_skip_conflicts:
            click.echo(f"[FAIL] 存在 {len(unresolved_conflicts)} 个未解决的冲突，请先解决冲突后再执行此操作，或使用 --skip-conflicts 跳过", err=True)
            click.echo("冲突列表:", err=True)
            for c in unresolved_conflicts:
                click.echo(f"  - [{c.conflict_type.value}] {c.message}", err=True)
            sys.exit(7)

        result = apply_corrections(config, storage, batch_id, correction_id, effective_limit, from_snapshot, effective_resume, effective_skip_conflicts)
        if effective_output_json:
            _print_json(result)
        else:
            click.echo(f"[OK] 修正应用完成")
            if profile:
                click.echo(f"  使用 Profile: {profile}")
            click.echo(f"  批次: {result['batch_name']} ({result['batch_id']})")
            click.echo(f"  批次执行ID: {result.get('apply_id', 'N/A')}")
            if result.get("from_snapshot"):
                click.echo(f"  从快照继续: {result['from_snapshot']}")
            stats = result.get("statistics", {})
            click.echo(f"  总计: {result['total_count']} 个")
            click.echo(f"  本次应用: {result['applied_count']} 个")
            click.echo(f"  本次跳过(已完成): {result['skipped_count']} 个")
            click.echo(f"  本次失败: {result['failed_count']} 个")
            click.echo(f"  本次冲突: {result['conflict_count']} 个")
            click.echo(f"  剩余未应用: {result['remaining_count']} 个")
            click.echo(f"  进度: {stats.get('completed', 0)}/{result['total_count']} ({stats.get('completed', 0) * 100 // max(result['total_count'], 1)}%)")
            if result.get("hash_mismatch_count", 0) > 0:
                click.echo(f"  哈希不一致: {result['hash_mismatch_count']} 个")

            if result.get('limit'):
                click.echo(f"  限制数量: {result['limit']}")
            if result.get('target_correction_id'):
                click.echo(f"  指定修正ID: {result['target_correction_id']}")

            click.echo("\n应用成功:")
            for corr in result["applied"]:
                click.echo(f"  [OK] [{corr['type']}] {corr['target']} (ID: {corr['id']})")
            if result.get("skipped"):
                click.echo("\n跳过(已完成):")
                for corr in result["skipped"]:
                    click.echo(f"  [SKIP] [{corr['type']}] {corr['target']} (ID: {corr['id']} - 已完成)")
            if result.get("conflicts"):
                click.echo("\n冲突(未覆盖):")
                for conf in result["conflicts"]:
                    click.echo(f"  [CONFLICT] [{conf.get('conflict_type', 'unknown')}] {conf.get('message', '')}")
                    click.echo(f"    修正ID: {conf['correction_id']}")
                    if conf.get("details"):
                        d = conf["details"]
                        if d.get("expected_hash"):
                            click.echo(f"    期望哈希: {d['expected_hash']}")
                        if d.get("actual_hash"):
                            click.echo(f"    实际哈希: {d['actual_hash']}")
                        if d.get("modified_at"):
                            click.echo(f"    修改时间: {d['modified_at']}")
            if result.get("failed"):
                click.echo("\n失败:")
                for fail in result["failed"]:
                    click.echo(f"  [FAIL] [{fail['correction_id']}] {fail['error']}")
            if result.get("hash_mismatches"):
                click.echo("\n源文件哈希不一致:")
                for hm in result["hash_mismatches"]:
                    click.echo(f"  [HASH_MISMATCH] {hm['source_path']}")
                    click.echo(f"    期望: {hm['expected_hash']}")
                    click.echo(f"    实际: {hm['actual_hash']}")

            if result["remaining_count"] > 0 and not result.get("target_correction_id"):
                click.echo(f"\n提示: 剩余 {result['remaining_count']} 项未执行，可再次运行 apply 继续")
        if result.get("hash_mismatch_count", 0) > 0:
            sys.exit(3)
        if result.get("conflict_count", 0) > 0:
            sys.exit(9)
        if result["failed_count"] > 0:
            sys.exit(1)
        sys.exit(0)
    except SystemExit:
        raise
    except Exception as e:
        click.echo(f"[FAIL] 应用失败: {e}", err=True)
        sys.exit(1)


@cli.command("undo")
@click.option("--batch-id", help="批次ID")
@click.option("--correction-id", help="指定要撤销的修正ID")
@click.option("--profile", help="使用保存的 Profile 配置")
@click.option("--json/--no-json", "output_json", default=None, help="输出JSON格式")
@click.pass_context
def undo(ctx, batch_id: Optional[str], correction_id: Optional[str], profile: Optional[str], output_json: Optional[bool]):
    """撤销已应用的修正，状态将正确回退"""
    profile_obj = _load_and_apply_profile(ctx, profile)
    effective_args = _get_effective_args(profile_obj, output_json=output_json)
    effective_output_json = effective_args["output_json"]

    config = ctx.obj["config"]
    storage = ctx.obj["storage"]

    try:
        batch, _ = get_or_create_batch(storage, batch_id)

        unresolved_conflicts = [c for c in batch.conflicts if not c.resolved]
        if unresolved_conflicts:
            click.echo(f"[FAIL] 存在 {len(unresolved_conflicts)} 个未解决的冲突，请先解决冲突后再执行此操作", err=True)
            click.echo("冲突列表:", err=True)
            for c in unresolved_conflicts:
                click.echo(f"  - [{c.conflict_type.value}] {c.message}", err=True)
            sys.exit(7)

        if not has_undoable_operations(batch):
            click.echo("[FAIL] 没有可撤销的操作", err=True)
            sys.exit(6)

        result = undo_corrections(config, storage, batch_id, correction_id)
        if effective_output_json:
            _print_json(result)
        else:
            click.echo(f"[OK] 撤销完成")
            if profile:
                click.echo(f"  使用 Profile: {profile}")
            click.echo(f"  批次: {result['batch_name']} ({result['batch_id']})")
            click.echo(f"  撤销执行ID: {result.get('undo_id', 'N/A')}")
            click.echo(f"  撤销前已应用: {result['total_applied_before']} 个")
            click.echo(f"  本次撤销: {result['undone_count']} 个")
            click.echo(f"  失败: {result['failed_count']} 个")
            click.echo(f"  剩余已应用: {result['remaining_applied_after']} 个")
            stats = result.get("statistics", {})
            click.echo(f"  当前状态: 待执行={stats.get('pending', 0)}, 已完成={stats.get('completed', 0)}, 已撤销={stats.get('rolled_back', 0)}")
            if result.get('target_correction_id'):
                click.echo(f"  指定修正ID: {result['target_correction_id']}")
            for corr in result["undone"]:
                click.echo(f"  [UNDO] [{corr['type']}] {corr['target']} (ID: {corr['id']})")
            for fail in result["failed"]:
                click.echo(f"  [FAIL] [{fail['correction_id']}] {fail['error']}")
        sys.exit(0)
    except SystemExit:
        raise
    except ValueError as e:
        if "没有可撤销的操作" in str(e):
            click.echo(f"[FAIL] {e}", err=True)
            sys.exit(6)
        click.echo(f"[FAIL] 撤销失败: {e}", err=True)
        sys.exit(1)
    except Exception as e:
        click.echo(f"[FAIL] 撤销失败: {e}", err=True)
        sys.exit(1)


@cli.command("status")
@click.option("--batch-id", help="批次ID")
@click.option("--profile", help="使用保存的 Profile 配置")
@click.option("--json/--no-json", "output_json", default=None, help="输出JSON格式")
@click.option("--details", is_flag=True, help="显示详细的修正项列表")
@click.pass_context
def status(ctx, batch_id: Optional[str], profile: Optional[str], output_json: Optional[bool], details: bool):
    """显示批次执行状态和进度统计"""
    profile_obj = _load_and_apply_profile(ctx, profile)
    effective_args = _get_effective_args(profile_obj, output_json=output_json)
    effective_output_json = effective_args["output_json"]

    config = ctx.obj["config"]
    storage = ctx.obj["storage"]

    try:
        result = get_batch_status(config, storage, batch_id)
        if effective_output_json:
            _print_json(result)
        else:
            click.echo(f"[OK] 批次状态")
            if profile:
                click.echo(f"  使用 Profile: {profile}")
            click.echo(f"  批次: {result['batch_name']} ({result['batch_id']})")
            click.echo(f"  创建时间: {result['created_at']}")
            click.echo(f"  更新时间: {result['updated_at']}")
            click.echo(f"  源文件数: {result['file_count']} 个")
            click.echo(f"  交付清单项: {result['delivery_count']} 个")
            click.echo(f"\n执行进度: {result['progress_percent']}% ({result['completed_count']}/{result['total_count']})")

            stats = result.get("statistics", {})
            click.echo(f"\n状态统计:")
            click.echo(f"  待执行: {stats.get('pending', 0)} 个")
            click.echo(f"  已完成: {stats.get('completed', 0)} 个")
            click.echo(f"  冲突: {stats.get('conflicted', 0)} 个")
            click.echo(f"  失败: {stats.get('failed', 0)} 个")
            click.echo(f"  跳过: {stats.get('skipped', 0)} 个")
            click.echo(f"  已撤销: {stats.get('rolled_back', 0)} 个")
            click.echo(f"  可撤销: {stats.get('undoable', 0)} 个")

            if result.get("last_plan_at"):
                click.echo(f"\n最后计划时间: {result['last_plan_at']}")
            if result.get("last_apply_at"):
                click.echo(f"最后执行时间: {result['last_apply_at']}")
            if result.get("last_undo_at"):
                click.echo(f"最后撤销时间: {result['last_undo_at']}")

            if result.get("snapshots"):
                click.echo(f"\n保存的批次快照 ({len(result['snapshots'])} 个):")
                for s in result["snapshots"]:
                    active_marker = " [当前]" if s.get("is_active") else ""
                    click.echo(f"  - {s['name']} ({s['snapshot_id']}){active_marker}")
                    click.echo(f"      创建时间: {s['created_at']}")
                    click.echo(f"      进度: {s['snapshot_progress']}% ({s['snapshot_completed']}/{s['snapshot_total']})")
                    click.echo(f"      待执行: {s['snapshot_pending']}, 已完成: {s['snapshot_completed']}, 冲突: {s['snapshot_conflicted']}")
                    if s.get("description"):
                        click.echo(f"      描述: {s['description']}")

            if result.get("unresolved_conflicts_count", 0) > 0:
                click.echo(f"\n[WARN] 存在 {result['unresolved_conflicts_count']} 个未解决的冲突:")
                for c in result.get("unresolved_conflicts", []):
                    click.echo(f"  - [{c['conflict_type']}] {c['message']}")

            if details:
                if result.get("pending"):
                    click.echo(f"\n待执行项 ({len(result['pending'])}):")
                    for c in result["pending"]:
                        click.echo(f"  [{c['id']}] [{c['type']}] {c['target']}")
                if result.get("completed"):
                    click.echo(f"\n已完成项 ({len(result['completed'])}):")
                    for c in result["completed"]:
                        click.echo(f"  [{c['id']}] [{c['type']}] {c['target']} (完成于 {c.get('completed_at', 'N/A')})")
                if result.get("conflicted"):
                    click.echo(f"\n冲突项 ({len(result['conflicted'])}):")
                    for c in result["conflicted"]:
                        click.echo(f"  [{c['id']}] [{c['type']}] {c['target']}")
                        if c.get("failure_reason"):
                            click.echo(f"    原因: {c['failure_reason']}")
                if result.get("failed"):
                    click.echo(f"\n失败项 ({len(result['failed'])}):")
                    for c in result["failed"]:
                        click.echo(f"  [{c['id']}] [{c['type']}] {c['target']}")
                        if c.get("failure_reason"):
                            click.echo(f"    原因: {c['failure_reason']}")

            click.echo(f"\n继续执行: photo-archive -c config.yaml apply")
            if result.get("active_snapshot_id"):
                click.echo(f"从快照继续: photo-archive -c config.yaml apply --from-snapshot {result['active_snapshot_id']}")
        sys.exit(0)
    except Exception as e:
        click.echo(f"[FAIL] 获取状态失败: {e}", err=True)
        sys.exit(1)


@cli.command("report")
@click.option("--batch-id", help="批次ID")
@click.option("--output", "-o", help="输出文件路径")
@click.option("--format", "fmt", default="json", type=click.Choice(["json", "csv"]), help="输出格式")
@click.option("--json", "output_json", is_flag=True, help="输出JSON格式到控制台")
@click.pass_context
def report(ctx, batch_id: Optional[str], output: Optional[str], fmt: str, output_json: bool):
    """导出校验报告"""
    config = ctx.obj["config"]
    storage = ctx.obj["storage"]

    try:
        result = generate_report(config, storage, batch_id, output, fmt)
        if output_json or not output:
            _print_json(result)
        if output:
            click.echo(f"[OK] 报告已导出到: {output}")

        summary = result["summary"]
        if not output_json:
            click.echo(f"\n  批次: {summary['batch_name']}")
            click.echo(f"  交付项: {summary['total_delivery_items']}")
            click.echo(f"  正常: {summary['ok_count']}, 缺片: {summary['missing_count']}, 重复: {summary['duplicate_count']}, 哈希不一致: {summary['hash_mismatch_count']}")
            click.echo(f"  已执行修正: {summary['active_corrections_count']} 个")
            if summary.get("unresolved_conflicts_count", 0) > 0:
                click.echo(f"  未解决冲突: {summary['unresolved_conflicts_count']} 个")
                click.echo("\n冲突列表:")
                for c in result.get("unresolved_conflicts", []):
                    click.echo(f"  - [{c['conflict_type']}] {c['message']}")

        if summary.get("unresolved_conflicts_count", 0) > 0:
            sys.exit(7)
        if summary["hash_mismatch_count"] > 0:
            sys.exit(3)
        if summary["duplicate_count"] > 0:
            sys.exit(4)
        if summary["missing_count"] > 0:
            sys.exit(5)
        sys.exit(0)
    except Exception as e:
        click.echo(f"[FAIL] 生成报告失败: {e}", err=True)
        sys.exit(1)


@cli.command("list-batches")
@click.option("--json", "output_json", is_flag=True, help="输出JSON格式")
@click.pass_context
def list_batches(ctx, output_json: bool):
    """列出所有批次"""
    storage = ctx.obj["storage"]
    batches = storage.list_batches()

    if output_json:
        _print_json(batches)
    else:
        if not batches:
            click.echo("(没有批次)")
        else:
            click.echo(f"共 {len(batches)} 个批次:")
            for b in batches:
                click.echo(f"  {b['batch_id']} - {b['name']}")
                click.echo(f"    文件: {b['file_count']}, 清单: {b['delivery_count']}, 修正: {b['correction_count']}")
                click.echo(f"    更新于: {b['updated_at']}")
    sys.exit(0)


@click.group()
def package():
    """交付包打包 - 生成可交付给客户的目录包"""
    pass


cli.add_command(package)


@package.command("create")
@click.argument("target_dir")
@click.option("--batch-id", help="批次ID，不指定则使用最新批次")
@click.option("--batch-name", help="按批次名称选择")
@click.option("--notes", help="交付说明备注")
@click.option("--include", multiple=True, help="仅包含指定文件名（可多次指定）")
@click.option("--exclude", multiple=True, help="排除指定文件名（可多次指定）")
@click.option("--camera", multiple=True, help="仅包含指定机位的照片（可多次指定）")
@click.option("--dry-run", is_flag=True, help="预览模式，只显示将复制的文件和占用空间，不实际执行")
@click.option("--json", "output_json", is_flag=True, help="输出JSON格式")
@click.option("--force", is_flag=True, help="强制覆盖已存在的目标目录（谨慎使用）")
@click.option("--skip-conflicts", is_flag=True, help="跳过文件冲突项，继续打包其他文件")
@click.pass_context
def package_create(
    ctx,
    target_dir: str,
    batch_id: Optional[str],
    batch_name: Optional[str],
    notes: str,
    include: tuple,
    exclude: tuple,
    camera: tuple,
    dry_run: bool,
    output_json: bool,
    force: bool,
    skip_conflicts: bool,
):
    """创建交付包 - 按批次选择照片，生成固定目录结构和校验文件"""
    config = ctx.obj["config"]
    storage = ctx.obj["storage"]
    package_storage = ctx.obj["package_storage"]

    try:
        result = create_delivery_package(
            config=config,
            storage=storage,
            package_storage=package_storage,
            target_dir=target_dir,
            batch_id=batch_id,
            batch_name=batch_name,
            notes=notes,
            include_filenames=list(include) if include else None,
            exclude_filenames=list(exclude) if exclude else None,
            include_cameras=list(camera) if camera else None,
            dry_run=dry_run,
            force=force,
            skip_conflicts=skip_conflicts,
        )
        if output_json:
            _print_json(result)
        else:
            if dry_run:
                click.echo(f"[PREVIEW] 打包预览（未实际执行）")
            else:
                if result["status"] == "completed":
                    click.echo(f"[OK] 打包完成")
                elif result["status"] == "failed":
                    click.echo(f"[FAIL] 打包失败")
                else:
                    click.echo(f"[WARN] 打包部分完成")

            click.echo(f"  打包ID: {result['package_id']}")
            click.echo(f"  批次: {result['batch_name']} ({result['batch_id']})")
            click.echo(f"  目标目录: {result['target_dir']}")
            click.echo(f"  总计文件: {result['total_files']} 个")
            click.echo(f"  总计大小: {_format_size(result['total_size'])}")
            click.echo(f"  已复制: {result['copied_files']} 个 ({_format_size(result['copied_size'])})")
            click.echo(f"  跳过: {result['skipped_files']} 个")
            click.echo(f"  失败: {result['failed_files']} 个")

            if result.get("manifest_path"):
                click.echo(f"  清单文件: {result['manifest_path']}")
            if result.get("checksum_path"):
                click.echo(f"  校验文件: {result['checksum_path']}")
            if result.get("readme_path"):
                click.echo(f"  交付说明: {result['readme_path']}")

            if result.get("items") and dry_run:
                click.echo("\n将复制的文件:")
                for item in result["items"][:20]:
                    click.echo(f"  + {item['file_name']} ({_format_size(item['size'])})")
                if len(result["items"]) > 20:
                    click.echo(f"  ... 还有 {len(result['items']) - 20} 个文件")

            if result.get("skipped_items"):
                click.echo("\n跳过的文件:")
                for item in result["skipped_items"]:
                    reason = item.get("skip_reason", "未知原因")
                    click.echo(f"  - {item['file_name']}: {reason}")

            if result.get("failed_items"):
                click.echo("\n失败的文件:")
                for fail in result["failed_items"]:
                    click.echo(f"  ! {fail.get('file_name', '未知文件')}: {fail.get('error', '未知错误')}")

            if dry_run:
                click.echo("\n提示: 去掉 --dry-run 即可实际执行打包")

        if result["status"] == "failed":
            sys.exit(1)
        if result["failed_files"] > 0 and not skip_conflicts:
            sys.exit(1)
        sys.exit(0)
    except PackageTargetExistsError as e:
        if output_json:
            _print_json(e.to_dict())
        else:
            click.echo(f"[FAIL] {e}", err=True)
            click.echo("\n提示: 使用 --force 可强制覆盖（谨慎使用）", err=True)
        sys.exit(11)
    except PackageFileConflictError as e:
        if output_json:
            _print_json(e.to_dict())
        else:
            click.echo(f"[FAIL] {e}", err=True)
            click.echo("\n提示: 使用 --skip-conflicts 可跳过冲突项继续打包", err=True)
        sys.exit(12)
    except PackageInsufficientSpaceError as e:
        if output_json:
            _print_json(e.to_dict())
        else:
            click.echo(f"[FAIL] {e}", err=True)
            click.echo(f"  需要: {_format_size(e.required)}", err=True)
            click.echo(f"  可用: {_format_size(e.available)}", err=True)
            click.echo(f"  缺少: {_format_size(e.required - e.available)}", err=True)
        sys.exit(13)
    except PackageSourceModifiedError as e:
        if output_json:
            _print_json(e.to_dict())
        else:
            click.echo(f"[FAIL] {e}", err=True)
        sys.exit(14)
    except Exception as e:
        click.echo(f"[FAIL] 打包失败: {e}", err=True)
        sys.exit(1)


@package.command("list")
@click.option("--batch-id", help="按批次ID筛选")
@click.option("--status", help="按状态筛选 (pending/in_progress/completed/failed/dry_run)")
@click.option("--limit", type=int, default=20, help="显示最近的 N 条记录")
@click.option("--json", "output_json", is_flag=True, help="输出JSON格式")
@click.pass_context
def package_list(ctx, batch_id: Optional[str], status: Optional[str], limit: int, output_json: bool):
    """查询打包历史记录（跨进程重启后仍可查看）"""
    package_storage = ctx.obj["package_storage"]

    try:
        result = list_packages(package_storage, batch_id, status, limit)
        if output_json:
            _print_json(result)
        else:
            if result["count"] == 0:
                click.echo("(没有打包记录)")
            else:
                click.echo(f"共 {result['count']} 条打包记录（最近 {limit} 条）:")
                for pkg in result["packages"]:
                    status_icon = {
                        "pending": "[·]",
                        "in_progress": "[→]",
                        "completed": "[✓]",
                        "failed": "[✗]",
                        "cancelled": "[×]",
                        "dry_run": "[P]",
                    }.get(pkg["status"], "[?]")
                    dry_run_marker = " (预览)" if pkg.get("dry_run") else ""
                    click.echo(f"  {status_icon} {pkg['package_id']} - {pkg['batch_name']}{dry_run_marker}")
                    click.echo(f"      目标: {pkg['target_dir']}")
                    click.echo(f"      状态: {pkg['status']}, 文件: {pkg['total_files']}, 大小: {_format_size(pkg['total_size'])}")
                    click.echo(f"      创建: {pkg['created_at']}")
                    if pkg.get("error_message"):
                        click.echo(f"      错误: {pkg['error_message']}")
        sys.exit(0)
    except Exception as e:
        click.echo(f"[FAIL] 查询失败: {e}", err=True)
        sys.exit(1)


@package.command("show")
@click.argument("package_id")
@click.option("--json", "output_json", is_flag=True, help="输出JSON格式")
@click.option("--files", is_flag=True, help="显示文件列表")
@click.pass_context
def package_show(ctx, package_id: str, output_json: bool, files: bool):
    """查看打包详情"""
    package_storage = ctx.obj["package_storage"]

    try:
        result = show_package(package_storage, package_id)
        if output_json:
            _print_json(result)
        else:
            click.echo(f"[OK] 打包详情")
            click.echo(f"  打包ID: {result['package_id']}")
            click.echo(f"  批次: {result['batch_name']} ({result['batch_id']})")
            click.echo(f"  目标目录: {result['target_dir']}")
            click.echo(f"  状态: {result['status']}")
            click.echo(f"  创建时间: {result['created_at']}")
            if result.get("started_at"):
                click.echo(f"  开始时间: {result['started_at']}")
            if result.get("completed_at"):
                click.echo(f"  完成时间: {result['completed_at']}")
            click.echo(f"  总计文件: {result['total_files']} 个")
            click.echo(f"  总计大小: {_format_size(result['total_size'])}")
            click.echo(f"  已复制: {result['copied_files']} 个 ({_format_size(result['copied_size'])})")
            click.echo(f"  跳过: {result['skipped_files']} 个")
            click.echo(f"  失败: {result['failed_files']} 个")

            if result.get("notes"):
                click.echo(f"  备注: {result['notes']}")
            if result.get("manifest_path"):
                click.echo(f"  清单文件: {result['manifest_path']}")
            if result.get("checksum_path"):
                click.echo(f"  校验文件: {result['checksum_path']}")
            if result.get("readme_path"):
                click.echo(f"  交付说明: {result['readme_path']}")
            if result.get("error_message"):
                click.echo(f"  错误: {result['error_message']}")

            if files:
                if result.get("items"):
                    click.echo(f"\n打包文件 ({len(result['items'])} 个):")
                    for item in result["items"]:
                        status = "[✓]" if item.get("copied") else "[·]"
                        click.echo(f"  {status} {item['file_name']} ({_format_size(item['size'])})")
                        click.echo(f"      源: {item['source_path']}")
                        click.echo(f"      目标: {item['target_path']}")
                        click.echo(f"      哈希: {item['hash'][:16]}...")

                if result.get("skipped_items"):
                    click.echo(f"\n跳过文件 ({len(result['skipped_items'])} 个):")
                    for item in result["skipped_items"]:
                        reason = item.get("skip_reason", "未知原因")
                        click.echo(f"  [SKIP] {item['file_name']}: {reason}")

                if result.get("failed_items"):
                    click.echo(f"\n失败文件 ({len(result['failed_items'])} 个):")
                    for fail in result["failed_items"]:
                        click.echo(f"  [FAIL] {fail.get('file_name', '未知文件')}: {fail.get('error', '未知错误')}")

        sys.exit(0)
    except ValueError as e:
        if output_json:
            _print_json({"error": str(e)})
        else:
            click.echo(f"[FAIL] {e}", err=True)
        sys.exit(1)
    except Exception as e:
        click.echo(f"[FAIL] 查询失败: {e}", err=True)
        sys.exit(1)


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


@click.group()
def profile():
    """管理可持久化运行配置 Profile"""
    pass


cli.add_command(profile)


@profile.command("save")
@click.argument("name")
@click.option("--description", "-d", default="", help="Profile 描述")
@click.option("--conflict-strategy", type=click.Choice(["fail", "skip", "overwrite"]), default="fail", help="冲突处理策略")
@click.option("--resume/--no-resume", default=True, help="是否跳过已完成项继续执行")
@click.option("--skip-conflicts", is_flag=True, help="跳过冲突项继续执行")
@click.option("--output-format", type=click.Choice(["text", "json"]), default="text", help="默认输出格式")
@click.option("--log-level", type=click.Choice(["DEBUG", "INFO", "WARNING", "ERROR"]), default="INFO", help="日志级别")
@click.option("--default-limit", type=int, help="默认执行数量限制")
@click.option("--overwrite", is_flag=True, help="覆盖已存在的同名 Profile")
@click.option("--json", "output_json", is_flag=True, help="输出JSON格式")
@click.pass_context
def profile_save(ctx, name: str, description: str, conflict_strategy: str, resume: bool, skip_conflicts: bool, output_format: str, log_level: str, default_limit: Optional[int], overwrite: bool, output_json: bool):
    """保存当前运行配置为 Profile"""
    config = ctx.obj["config"]
    profile_storage = ctx.obj["profile_storage"]

    try:
        result = save_profile(
            config, profile_storage, name, description,
            conflict_strategy, resume, skip_conflicts, output_format, log_level, default_limit, overwrite
        )
        if output_json:
            _print_json(result)
        else:
            click.echo(f"[OK] Profile 已{result['operation']}: {name}")
            click.echo(f"  描述: {description or '(无)'}")
            click.echo(f"  冲突策略: {conflict_strategy}")
            click.echo(f"  自动续跑: {'是' if resume else '否'}")
            click.echo(f"  跳过冲突: {'是' if skip_conflicts else '否'}")
            click.echo(f"  输出格式: {output_format}")
            click.echo(f"  日志级别: {log_level}")
            if default_limit:
                click.echo(f"  默认限制: {default_limit} 项")
        sys.exit(0)
    except ProfileNameConflictError as e:
        if output_json:
            _print_json(e.to_dict())
        else:
            click.echo(f"[FAIL] Profile 名称冲突: {e}", err=True)
        sys.exit(10)
    except Exception as e:
        click.echo(f"[FAIL] 保存 Profile 失败: {e}", err=True)
        sys.exit(1)


@profile.command("load")
@click.argument("name")
@click.option("--json", "output_json", is_flag=True, help="输出JSON格式")
@click.pass_context
def profile_load(ctx, name: str, output_json: bool):
    """查看 Profile 详细配置"""
    profile_storage = ctx.obj["profile_storage"]

    try:
        result = load_profile(profile_storage, name)
        if output_json:
            _print_json(result)
        else:
            p = result["profile"]
            click.echo(f"[OK] Profile: {name}")
            click.echo(f"  描述: {p.get('description', '(无)')}")
            click.echo(f"  版本: {p.get('version', 1)}")
            click.echo(f"  创建时间: {p.get('created_at', 'N/A')}")
            click.echo(f"  更新时间: {p.get('updated_at', 'N/A')}")
            click.echo(f"\n归档规则:")
            click.echo(f"  命名规则: {p.get('naming_rule', 'N/A')}")
            click.echo(f"  机位: {', '.join(p.get('cameras', []))}")
            click.echo(f"  哈希算法: {p.get('hash_strategy', 'N/A')}")
            click.echo(f"  归档目录: {p.get('archive_dir', 'N/A')}")
            click.echo(f"  工作目录: {p.get('work_dir', 'N/A')}")
            click.echo(f"\n运行策略:")
            click.echo(f"  冲突策略: {p.get('conflict_strategy', 'N/A')}")
            click.echo(f"  自动续跑: {'是' if p.get('resume', True) else '否'}")
            click.echo(f"  跳过冲突: {'是' if p.get('skip_conflicts', False) else '否'}")
            click.echo(f"\n输出配置:")
            click.echo(f"  输出格式: {p.get('output_format', 'N/A')}")
            click.echo(f"  日志级别: {p.get('log_level', 'N/A')}")
            if p.get('default_limit'):
                click.echo(f"  默认限制: {p['default_limit']} 项")
        sys.exit(0)
    except ValueError as e:
        if output_json:
            _print_json({"error": str(e)})
        else:
            click.echo(f"[FAIL] {e}", err=True)
        sys.exit(1)


@profile.command("list")
@click.option("--json", "output_json", is_flag=True, help="输出JSON格式")
@click.pass_context
def profile_list(ctx, output_json: bool):
    """列出所有 Profile"""
    profile_storage = ctx.obj["profile_storage"]

    try:
        result = list_profiles(profile_storage)
        if output_json:
            _print_json(result)
        else:
            if result["count"] == 0:
                click.echo("(没有保存的 Profile)")
            else:
                click.echo(f"共 {result['count']} 个 Profile:")
                for p in result["profiles"]:
                    click.echo(f"  {p['name']} - {p.get('description', '(无描述)')}")
                    click.echo(f"    冲突策略: {p.get('conflict_strategy', 'N/A')}, 输出: {p.get('output_format', 'N/A')}")
                    click.echo(f"    更新于: {p.get('updated_at', 'N/A')}")
        sys.exit(0)
    except Exception as e:
        click.echo(f"[FAIL] 列出 Profile 失败: {e}", err=True)
        sys.exit(1)


@profile.command("delete")
@click.argument("name")
@click.option("--json", "output_json", is_flag=True, help="输出JSON格式")
@click.pass_context
def profile_delete(ctx, name: str, output_json: bool):
    """删除指定 Profile"""
    profile_storage = ctx.obj["profile_storage"]

    try:
        result = delete_profile(profile_storage, name)
        if output_json:
            _print_json(result)
        else:
            click.echo(f"[OK] Profile 已删除: {name}")
        sys.exit(0)
    except ValueError as e:
        if output_json:
            _print_json({"error": str(e)})
        else:
            click.echo(f"[FAIL] {e}", err=True)
        sys.exit(1)


@profile.command("export")
@click.argument("name")
@click.argument("output_path")
@click.option("--json", "output_json", is_flag=True, help="输出JSON格式")
@click.pass_context
def profile_export(ctx, name: str, output_path: str, output_json: bool):
    """导出 Profile 到 JSON 文件"""
    profile_storage = ctx.obj["profile_storage"]

    try:
        result = export_profile(profile_storage, name, output_path)
        if output_json:
            _print_json(result)
        else:
            click.echo(f"[OK] Profile 已导出: {name} -> {output_path}")
        sys.exit(0)
    except ValueError as e:
        if output_json:
            _print_json({"error": str(e)})
        else:
            click.echo(f"[FAIL] {e}", err=True)
        sys.exit(1)


@profile.command("import")
@click.argument("import_path")
@click.option("--overwrite", is_flag=True, help="覆盖已存在的同名 Profile")
@click.option("--rename", help="重命名导入的 Profile")
@click.option("--json", "output_json", is_flag=True, help="输出JSON格式")
@click.pass_context
def profile_import(ctx, import_path: str, overwrite: bool, rename: Optional[str], output_json: bool):
    """从 JSON 文件导入 Profile"""
    profile_storage = ctx.obj["profile_storage"]

    try:
        result = import_profile(profile_storage, import_path, overwrite, rename)
        if output_json:
            _print_json(result)
        else:
            click.echo(f"[OK] Profile 已导入: {result['profile_name']}")
            if rename:
                click.echo(f"  重命名为: {rename}")
        sys.exit(0)
    except ProfileNameConflictError as e:
        if output_json:
            _print_json(e.to_dict())
        else:
            click.echo(f"[FAIL] Profile 名称冲突: {e}", err=True)
            click.echo("提示: 使用 --overwrite 覆盖或 --rename 重命名", err=True)
        sys.exit(10)
    except FileNotFoundError as e:
        if output_json:
            _print_json({"error": str(e)})
        else:
            click.echo(f"[FAIL] {e}", err=True)
        sys.exit(1)
    except Exception as e:
        if output_json:
            _print_json({"error": str(e)})
        else:
            click.echo(f"[FAIL] 导入 Profile 失败: {e}", err=True)
        sys.exit(1)


@profile.command("audit-log")
@click.option("--limit", type=int, default=100, help="显示最近的 N 条记录")
@click.option("--json", "output_json", is_flag=True, help="输出JSON格式")
@click.pass_context
def profile_audit_log(ctx, limit: int, output_json: bool):
    """查看 Profile 操作审计日志"""
    profile_storage = ctx.obj["profile_storage"]

    try:
        result = get_audit_log(profile_storage, limit)
        if output_json:
            _print_json(result)
        else:
            if result["count"] == 0:
                click.echo("(没有审计记录)")
            else:
                click.echo(f"最近 {result['count']} 条操作记录:")
                for entry in result["entries"]:
                    status = "[OK]" if entry["success"] else "[FAIL]"
                    profile_name = entry.get("profile_name", "N/A")
                    click.echo(f"  {entry['timestamp']} {status} {entry['operation']} - {profile_name}")
                    if not entry["success"] and entry.get("error_message"):
                        click.echo(f"    错误: {entry['error_message']}")
                    if entry.get("details"):
                        for k, v in entry["details"].items():
                            click.echo(f"    {k}: {v}")
        sys.exit(0)
    except Exception as e:
        click.echo(f"[FAIL] 获取审计日志失败: {e}", err=True)
        sys.exit(1)


@cli.command("help-exit-codes")
def help_exit_codes():
    """显示非零退出码说明"""
    click.echo("非零退出码说明:")
    for code in sorted(EXIT_CODES.keys()):
        click.echo(f"  {code} - {EXIT_CODES[code]}")
    sys.exit(0)


@click.group()
def acceptance():
    """交付验收审计 - 收包前完整检查照片和配置"""
    pass


cli.add_command(acceptance)


@acceptance.command("run")
@click.argument("acceptance_config")
@click.option("--json", "export_json", help="导出 JSON 报告的路径")
@click.option("--csv", "export_csv", help="导出 CSV 报告的路径")
@click.option("--overwrite", is_flag=True, help="覆盖已存在的导出文件")
@click.option("--no-summary", is_flag=True, help="不显示文本摘要")
@click.option("--output-json", is_flag=True, help="输出 JSON 格式到控制台")
@click.pass_context
def acceptance_run(
    ctx,
    acceptance_config: str,
    export_json: Optional[str],
    export_csv: Optional[str],
    overwrite: bool,
    no_summary: bool,
    output_json: bool,
):
    """运行交付验收审计，按配置检查目录结构、必备文件、照片数量等"""
    acceptance_storage = ctx.obj["acceptance_storage"]

    try:
        config = load_acceptance_config(acceptance_config)
        result = run_acceptance_audit(
            config=config,
            storage=acceptance_storage,
            export_json=export_json,
            export_csv=export_csv,
            overwrite=overwrite,
        )

        if output_json:
            _print_json(result)
        elif not no_summary:
            click.echo(result["text_summary"])

        if export_json:
            click.echo(f"\n[OK] JSON 报告已导出: {export_json}")
        if export_csv:
            click.echo(f"[OK] CSV 报告已导出: {export_csv}")

        stats = result["statistics"]
        if stats["failed"] > 0:
            sys.exit(20)
        sys.exit(0)

    except AcceptanceConfigError as e:
        if output_json:
            _print_json(e.to_dict())
        else:
            click.echo(f"[FAIL] 验收配置错误: {e}", err=True)
            if e.missing_fields:
                click.echo(f"  缺少字段: {', '.join(e.missing_fields)}", err=True)
        sys.exit(15)
    except AcceptanceRuleConflictError as e:
        if output_json:
            _print_json(e.to_dict())
        else:
            click.echo(f"[FAIL] 验收规则冲突: {e}", err=True)
            if e.conflicting_rules:
                for rule in e.conflicting_rules:
                    click.echo(f"  - {rule}", err=True)
        sys.exit(16)
    except AcceptanceDirectoryNotFoundError as e:
        if output_json:
            _print_json(e.to_dict())
        else:
            click.echo(f"[FAIL] 目录不存在: {e}", err=True)
        sys.exit(17)
    except AcceptanceExportExistsError as e:
        if output_json:
            _print_json(e.to_dict())
        else:
            click.echo(f"[FAIL] 导出文件已存在: {e}", err=True)
            click.echo("提示: 使用 --overwrite 可强制覆盖", err=True)
        sys.exit(18)
    except AcceptanceWritePermissionError as e:
        if output_json:
            _print_json(e.to_dict())
        else:
            click.echo(f"[FAIL] 写入权限错误: {e}", err=True)
        sys.exit(19)
    except Exception as e:
        if output_json:
            _print_json({"error": str(e)})
        else:
            click.echo(f"[FAIL] 验收审计失败: {e}", err=True)
        sys.exit(1)


@acceptance.command("list")
@click.option("--batch", "batch_name", help="按批次名称筛选")
@click.option("--client", "client_name", help="按客户名称筛选")
@click.option("--limit", type=int, default=10, help="显示最近的 N 条记录")
@click.option("--json", "output_json", is_flag=True, help="输出 JSON 格式")
@click.pass_context
def acceptance_list(ctx, batch_name: Optional[str], client_name: Optional[str], limit: int, output_json: bool):
    """查询验收审计历史记录（跨进程重启后仍可查看）"""
    acceptance_storage = ctx.obj["acceptance_storage"]

    try:
        result = list_audit_history(acceptance_storage, batch_name, client_name, limit)
        if output_json:
            _print_json(result)
        else:
            if result["count"] == 0:
                click.echo("(没有验收审计记录)")
            else:
                filter_info = ""
                if batch_name:
                    filter_info = f"（批次: {batch_name}）"
                elif client_name:
                    filter_info = f"（客户: {client_name}）"
                click.echo(f"共 {result['count']} 条验收审计记录{filter_info}（最近 {limit} 条）:")
                for audit in result["audits"]:
                    status_icon = {
                        "pass": "[✓]",
                        "fail": "[✗]",
                        "warning": "[!]",
                        "pending": "[·]",
                    }.get(audit["status"], "[?]")
                    stats = audit["statistics"]
                    click.echo(f"  {status_icon} {audit['audit_id']} - {audit['client_name']} / {audit['batch_name']}")
                    click.echo(f"      源目录: {audit['source_dir']}")
                    click.echo(f"      时间: {audit['started_at']}")
                    click.echo(f"      状态: {audit['status']}, 通过: {stats['passed']}, 失败: {stats['failed']}, 警告: {stats['warnings']}")
                    if audit.get("exported_paths"):
                        for fmt, path in audit["exported_paths"].items():
                            click.echo(f"      {fmt.upper()}: {path}")
        sys.exit(0)
    except Exception as e:
        click.echo(f"[FAIL] 查询历史记录失败: {e}", err=True)
        sys.exit(1)


@acceptance.command("show")
@click.argument("audit_id")
@click.option("--json", "output_json", is_flag=True, help="输出 JSON 格式")
@click.option("--no-summary", is_flag=True, help="不显示文本摘要")
@click.option("--details", is_flag=True, help="显示详细的检查结果")
@click.option("--logs", is_flag=True, help="显示审计日志")
@click.pass_context
def acceptance_show(ctx, audit_id: str, output_json: bool, no_summary: bool, details: bool, logs: bool):
    """查看验收审计详情"""
    acceptance_storage = ctx.obj["acceptance_storage"]

    try:
        result = get_audit_detail(acceptance_storage, audit_id)
        if output_json:
            _print_json(result)
        else:
            if not no_summary:
                click.echo(result["text_summary"])

            if details and result.get("results"):
                click.echo("\n" + "=" * 60)
                click.echo("  检查结果详情")
                click.echo("=" * 60)
                for r in result["results"]:
                    status_icon = {"pass": "✓", "fail": "✗", "warning": "!"}.get(r["status"], "?")
                    click.echo(f"\n{status_icon} [{r['rule_type']}] {r['rule_id']}: {r['message']}")
                    if r.get("details"):
                        click.echo("  详细数据:")
                        for d in r["details"][:5]:
                            click.echo(f"    - {json.dumps(d, ensure_ascii=False)}")
                        if len(r["details"]) > 5:
                            click.echo(f"    ... 还有 {len(r['details']) - 5} 条记录")

            if logs and result.get("log_entries"):
                click.echo("\n" + "=" * 60)
                click.echo("  审计日志")
                click.echo("=" * 60)
                for entry in result["log_entries"]:
                    status = "[OK]" if entry.get("success", True) else "[FAIL]"
                    click.echo(f"  {entry.get('timestamp', '')} {status} {entry.get('operation', '')}")
                    if entry.get("details"):
                        for k, v in entry["details"].items():
                            click.echo(f"    {k}: {v}")

        sys.exit(0)
    except ValueError as e:
        click.echo(f"[FAIL] {e}", err=True)
        sys.exit(1)
    except Exception as e:
        click.echo(f"[FAIL] 获取详情失败: {e}", err=True)
        sys.exit(1)


@acceptance.command("reexport")
@click.argument("audit_id")
@click.option("--json", "export_json", help="导出 JSON 报告的路径")
@click.option("--csv", "export_csv", help="导出 CSV 报告的路径")
@click.option("--overwrite", is_flag=True, help="覆盖已存在的导出文件")
@click.option("--json-output", "output_json", is_flag=True, help="输出 JSON 格式到控制台")
@click.pass_context
def acceptance_reexport(
    ctx,
    audit_id: str,
    export_json: Optional[str],
    export_csv: Optional[str],
    overwrite: bool,
    output_json: bool,
):
    """重新导出历史验收审计结果"""
    acceptance_storage = ctx.obj["acceptance_storage"]

    try:
        if not export_json and not export_csv:
            click.echo("[FAIL] 请至少指定一个导出格式（--json 或 --csv）", err=True)
            sys.exit(1)

        result = reexport_audit_result(
            storage=acceptance_storage,
            audit_id=audit_id,
            export_json=export_json,
            export_csv=export_csv,
            overwrite=overwrite,
        )

        if output_json:
            _print_json(result)
        else:
            click.echo(f"[OK] 审计记录 {audit_id} 已重新导出")
            for fmt, path in result["exported_paths"].items():
                click.echo(f"  {fmt.upper()}: {path}")

        sys.exit(0)

    except ValueError as e:
        click.echo(f"[FAIL] {e}", err=True)
        sys.exit(1)
    except AcceptanceExportExistsError as e:
        if output_json:
            _print_json(e.to_dict())
        else:
            click.echo(f"[FAIL] 导出文件已存在: {e}", err=True)
            click.echo("提示: 使用 --overwrite 可强制覆盖", err=True)
        sys.exit(18)
    except AcceptanceWritePermissionError as e:
        if output_json:
            _print_json(e.to_dict())
        else:
            click.echo(f"[FAIL] 写入权限错误: {e}", err=True)
        sys.exit(19)
    except Exception as e:
        click.echo(f"[FAIL] 重新导出失败: {e}", err=True)
        sys.exit(1)


if __name__ == "__main__":
    cli(obj={})
