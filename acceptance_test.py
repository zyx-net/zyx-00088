#!/usr/bin/env python
"""验收测试：跑通完整的 CLI 链路

测试流程:
init-sample → scan → 连续 import-list (2次) → verify → plan → apply → undo → report
"""
from pathlib import Path
import subprocess
import sys
import os
import json
import shutil


TEST_DIR = Path(__file__).parent.resolve()
ACCEPTANCE_DIR = TEST_DIR / "acceptance_test_data"
WORK_DIR = ACCEPTANCE_DIR / "work"
ARCHIVE_DIR = ACCEPTANCE_DIR / "archive"


def run_cmd(args, desc=""):
    """运行命令并打印结果"""
    cmd = [sys.executable, "-m", "photo_archive.cli"] + args
    env = os.environ.copy()
    env["PYTHONPATH"] = str(TEST_DIR)
    env["PYTHONIOENCODING"] = "utf-8"

    print(f"\n{'='*60}")
    print(f"→ {desc}")
    print(f"  命令: {' '.join(cmd)}")
    print("-" * 60)

    result = subprocess.run(
        cmd,
        cwd=TEST_DIR,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        env=env,
    )

    if result.stdout:
        print(result.stdout)
    if result.stderr:
        print(f"[STDERR] {result.stderr}", file=sys.stderr)
    print(f"  退出码: {result.returncode}")

    return result


def create_config(config_path):
    with open(config_path, "w", encoding="utf-8") as f:
        f.write('naming_rule: "{机位}_{批次}_{序号:04d}.{扩展名}"\n')
        f.write("cameras:\n  - A\n  - B\n  - C\n")
        f.write("hash_strategy: sha256\n")
        f.write(f"archive_dir: {ARCHIVE_DIR}\n")
        f.write(f"work_dir: {WORK_DIR}\n")


def create_manifest(path, entries):
    with open(path, "w", encoding="utf-8") as f:
        f.write("target_name,camera,sequence\n")
        for target, cam, seq in entries:
            f.write(f"{target},{cam},{seq}\n")


def main():
    print("\n" + "=" * 60)
    print("相册归档 CLI 多清单合并验收测试")
    print("=" * 60)

    for d in [ACCEPTANCE_DIR, WORK_DIR, ARCHIVE_DIR]:
        if d.exists():
            shutil.rmtree(d)
    ACCEPTANCE_DIR.mkdir(parents=True, exist_ok=True)

    print("\n[步骤 1] init-sample - 初始化样例数据")
    result = run_cmd([
        "init-sample", str(ACCEPTANCE_DIR / "sample"),
        "--cameras", "3",
        "--photos", "3",
        "--batch", "ACCEPT001",
    ], "init-sample")
    assert result.returncode == 0, f"init-sample 失败: {result.stderr}"

    config_path = ACCEPTANCE_DIR / "sample" / "config.yaml"
    create_config(config_path)

    source_dir = ACCEPTANCE_DIR / "sample" / "source_cards"
    delivery_dir = ACCEPTANCE_DIR / "sample" / "delivery_list"

    manifest1_path = delivery_dir / "manifest_batch1.csv"
    create_manifest(manifest1_path, [
        ("A_ACCEPT001_0001.jpg", "A", 1),
        ("A_ACCEPT001_0002.jpg", "A", 2),
        ("B_ACCEPT001_0001.jpg", "B", 1),
    ])

    manifest2_path = delivery_dir / "manifest_batch2.csv"
    create_manifest(manifest2_path, [
        ("B_ACCEPT001_0002.jpg", "B", 2),
        ("C_ACCEPT001_0001.jpg", "C", 1),
        ("C_ACCEPT001_0002.jpg", "C", 2),
    ])

    print("\n[步骤 2] scan - 扫描源目录")
    result = run_cmd([
        "-c", str(config_path),
        "scan", str(source_dir),
        "--batch-name", "acceptance-batch",
    ], "scan")
    assert result.returncode == 0, f"scan 失败: {result.stderr}"

    print("\n[步骤 3] import-list (第1次) - 导入第一批清单")
    result = run_cmd([
        "-c", str(config_path),
        "import-list", str(manifest1_path),
        "--json",
    ], "import-list #1")
    assert result.returncode == 0, f"import-list #1 失败: {result.stderr}"
    data1 = json.loads(result.stdout)
    assert data1["imported_count"] == 3
    assert data1["conflict_count"] == 0
    assert data1["total_delivery_items"] == 3
    print(f"  [OK] 成功导入 {data1['imported_count']} 个条目")

    print("\n[步骤 4] import-list (第2次) - 导入第二批清单（合并）")
    result = run_cmd([
        "-c", str(config_path),
        "import-list", str(manifest2_path),
        "--json",
    ], "import-list #2")
    assert result.returncode == 0, f"import-list #2 失败: {result.stderr}"
    data2 = json.loads(result.stdout)
    assert data2["imported_count"] == 3
    assert data2["conflict_count"] == 0
    assert data2["total_delivery_items"] == 6
    print(f"  [OK] 成功导入 {data2['imported_count']} 个条目，总计 {data2['total_delivery_items']} 个")

    print("\n[步骤 5] verify - 校验交付清单与源文件的匹配")
    result = run_cmd([
        "-c", str(config_path),
        "verify",
        "--json",
    ], "verify")
    assert result.returncode in [0, 4, 5], f"verify 失败: {result.stderr}"
    verify_data = json.loads(result.stdout)
    print(f"  [OK] 校验完成 - 正常: {verify_data['ok_count']}, 缺片: {verify_data['missing_count']}, 重复: {verify_data['duplicate_count']}")

    print("\n[步骤 6] plan - 生成修正计划")
    result = run_cmd([
        "-c", str(config_path),
        "plan",
        "--json",
    ], "plan")
    assert result.returncode == 0, f"plan 失败: {result.stderr}"
    plan_data = json.loads(result.stdout)
    assert plan_data["correction_count"] > 0
    print(f"  [OK] 生成 {plan_data['correction_count']} 个修正项")

    print("\n[步骤 7] apply - 应用修正计划")
    result = run_cmd([
        "-c", str(config_path),
        "apply",
        "--json",
    ], "apply")
    assert result.returncode in [0, 3], f"apply 失败: {result.stderr}"
    apply_data = json.loads(result.stdout)
    print(f"  [OK] 成功应用 {apply_data['applied_count']} 个修正")

    archive_batch_dir = ARCHIVE_DIR / "acceptance-batch"
    archived_files = list(archive_batch_dir.glob("*.jpg"))
    print(f"  [OK] 归档目录文件数: {len(archived_files)}")
    assert len(archived_files) >= apply_data["applied_count"] - apply_data.get("hash_mismatch_count", 0)

    print("\n[步骤 8] undo - 撤销已应用的修正")
    result = run_cmd([
        "-c", str(config_path),
        "undo",
        "--json",
    ], "undo")
    assert result.returncode == 0, f"undo 失败: {result.stderr}"
    undo_data = json.loads(result.stdout)
    print(f"  [OK] 撤销 {undo_data['undone_count']} 个修正")

    archived_files_after = list(archive_batch_dir.glob("*.jpg"))
    print(f"  [OK] 撤销后归档目录文件数: {len(archived_files_after)}")
    assert len(archived_files_after) == len(archived_files) - undo_data["undone_count"]

    print("\n[步骤 9] report - 生成报告")
    report_path = ACCEPTANCE_DIR / "acceptance_report.json"
    result = run_cmd([
        "-c", str(config_path),
        "report",
        "--output", str(report_path),
        "--format", "json",
    ], "report")
    assert result.returncode in [0, 3, 4, 5, 7], f"report 失败: {result.stderr}"

    with open(report_path, "r", encoding="utf-8") as f:
        report_data = json.load(f)

    print(f"  [OK] 报告已生成: {report_path}")
    print(f"    批次: {report_data['summary']['batch_name']}")
    print(f"    总交付项: {report_data['summary']['total_delivery_items']}")
    print(f"    导入次数: {report_data['summary']['total_imports']}")
    print(f"    活跃修正: {report_data['summary']['active_corrections_count']}")
    print(f"    已撤销修正: {report_data['summary']['undone_corrections_count']}")
    print(f"    冲突数: {report_data['summary']['unresolved_conflicts_count']}")

    assert report_data["summary"]["total_imports"] == 2
    assert report_data["summary"]["total_delivery_items"] == 6
    assert report_data["summary"]["active_corrections_count"] == 0
    assert report_data["summary"]["undone_corrections_count"] == undo_data["undone_count"]
    assert len(report_data["import_records"]) == 2

    csv_report_path = ACCEPTANCE_DIR / "acceptance_report.csv"
    result = run_cmd([
        "-c", str(config_path),
        "report",
        "--output", str(csv_report_path),
        "--format", "csv",
    ], "report (CSV)")
    assert result.returncode in [0, 3, 4, 5, 7], f"CSV report 失败: {result.stderr}"
    assert csv_report_path.exists()

    with open(csv_report_path, "r", encoding="utf-8-sig") as f:
        csv_content = f.read()
    assert "=== 导入记录 ===" in csv_content
    assert "=== 文件映射 ===" in csv_content
    print(f"  [OK] CSV 报告已生成: {csv_report_path}")

    print("\n" + "=" * 60)
    print("[OK] 验收测试全部通过！")
    print("=" * 60)
    print(f"\n测试数据目录: {ACCEPTANCE_DIR}")
    print(f"JSON 报告: {report_path}")
    print(f"CSV 报告: {csv_report_path}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except AssertionError as e:
        print(f"\n[FAIL] 验收测试失败: {e}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"\n[FAIL] 验收测试异常: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        sys.exit(1)
