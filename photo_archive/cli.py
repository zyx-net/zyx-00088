from pathlib import Path
from typing import Optional
import json
import sys

import click

from .config import Config
from .storage import BatchStorage
from .models import BatchNameConflictError
from .commands.init_sample import generate_sample_data
from .commands.scan import scan_directory
from .commands.import_list import import_delivery_list
from .commands.verify import verify_batch
from .commands.plan import generate_correction_plan
from .commands.apply import apply_corrections, apply_single_correction
from .commands.undo import undo_corrections, has_undoable_operations
from .commands.report import generate_report
from .commands.utils import get_or_create_batch


def _get_config(config_path: str) -> Config:
    return Config.load(config_path)


def _get_storage(config: Config) -> BatchStorage:
    return BatchStorage(config.work_dir)


def _print_json(data) -> None:
    click.echo(json.dumps(data, ensure_ascii=False, indent=2))


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
    """
    ctx.ensure_object(dict)
    cfg = _get_config(config)
    ctx.obj["config"] = cfg
    ctx.obj["storage"] = _get_storage(cfg)


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
@click.pass_context
def import_list(ctx, manifest_path: str, batch_id: Optional[str], batch_name: Optional[str], output_json: bool, merge: bool):
    """导入交付清单 (CSV 格式)"""
    config = ctx.obj["config"]
    storage = ctx.obj["storage"]

    try:
        result = import_delivery_list(config, storage, manifest_path, batch_id, batch_name, merge)
        conflict_count = result.get("conflict_count", 0)

        if output_json:
            _print_json(result)
        else:
            if conflict_count > 0:
                click.echo(f"[WARN] 导入完成但存在冲突")
                click.echo(f"  批次: {result['batch_name']} ({result['batch_id']})")
                click.echo(f"  清单文件: {result['source_file']}")
                click.echo(f"  导入条目: {result['imported_count']} 个")
                click.echo(f"  冲突数量: {conflict_count} 个")
                click.echo("\n冲突列表:")
                for c in result.get("conflicts", []):
                    click.echo(f"  - [{c['conflict_type']}] {c['message']}")
            else:
                click.echo(f"[OK] 导入完成")
                click.echo(f"  批次: {result['batch_name']} ({result['batch_id']})")
                click.echo(f"  清单文件: {result['source_file']}")
                click.echo(f"  导入条目: {result['imported_count']} 个")

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
@click.option("--json", "output_json", is_flag=True, help="输出JSON格式")
@click.pass_context
def plan(ctx, batch_id: Optional[str], output_json: bool):
    """生成修正计划"""
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

        result = generate_correction_plan(config, storage, batch_id)
        if output_json:
            _print_json(result)
        else:
            click.echo(f"[OK] 修正计划已生成")
            click.echo(f"  批次: {result['batch_name']} ({result['batch_id']})")
            click.echo(f"  归档目录: {result['archive_dir']}")
            click.echo(f"  修正项: {result['correction_count']} 个")
            for i, corr in enumerate(result["corrections"], 1):
                click.echo(f"  {i}. [{corr['type']}] {corr['reason']}")
                if corr["source"]:
                    click.echo(f"     源: {corr['source']}")
                click.echo(f"     目标: {corr['target']}")
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
@click.option("--json", "output_json", is_flag=True, help="输出JSON格式")
@click.pass_context
def apply(ctx, batch_id: Optional[str], correction_id: Optional[str], limit: Optional[int], output_json: bool):
    """应用修正计划"""
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

        result = apply_corrections(config, storage, batch_id, correction_id, limit)
        if output_json:
            _print_json(result)
        else:
            click.echo(f"[OK] 修正应用完成")
            click.echo(f"  批次: {result['batch_name']} ({result['batch_id']})")
            click.echo(f"  成功应用: {result['applied_count']} 个")
            click.echo(f"  失败: {result['failed_count']} 个")
            if result.get("hash_mismatch_count", 0) > 0:
                click.echo(f"  哈希不一致: {result['hash_mismatch_count']} 个")
            for corr in result["applied"]:
                click.echo(f"  [OK] [{corr['type']}] {corr['target']}")
            for fail in result["failed"]:
                click.echo(f"  [FAIL] [{fail['correction_id']}] {fail['error']}")
            for hm in result.get("hash_mismatches", []):
                click.echo(f"  [HASH_MISMATCH] {hm['source_path']}")
                click.echo(f"    期望: {hm['expected_hash']}")
                click.echo(f"    实际: {hm['actual_hash']}")
        if result.get("hash_mismatch_count", 0) > 0:
            sys.exit(3)
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
@click.option("--json", "output_json", is_flag=True, help="输出JSON格式")
@click.pass_context
def undo(ctx, batch_id: Optional[str], correction_id: Optional[str], output_json: bool):
    """撤销已应用的修正"""
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
        if output_json:
            _print_json(result)
        else:
            click.echo(f"[OK] 撤销完成")
            click.echo(f"  批次: {result['batch_name']} ({result['batch_id']})")
            click.echo(f"  已撤销: {result['undone_count']} 个")
            click.echo(f"  失败: {result['failed_count']} 个")
            for corr in result["undone"]:
                click.echo(f"  [UNDO] [{corr['type']}] {corr['target']}")
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


@cli.command("help-exit-codes")
def help_exit_codes():
    """显示非零退出码说明"""
    click.echo("非零退出码说明:")
    for code in sorted(EXIT_CODES.keys()):
        click.echo(f"  {code} - {EXIT_CODES[code]}")
    sys.exit(0)


if __name__ == "__main__":
    cli(obj={})
