# Photo Archive - 多命令离线相册归档校验 CLI

帮助摄影工作室把存储卡文件整理到客户交付批次的命令行工具。

## 功能特性

- **初始化样例**：生成测试用的存储卡目录、交付清单和配置文件
- **扫描目录**：扫描存储卡照片，计算哈希、识别机位信息
- **导入交付清单**：导入 CSV 格式的交付清单
- **校验**：检测缺片、重复文件、哈希不一致
- **生成修正计划**：根据校验结果生成文件复制/重命名计划
- **应用修正**：执行修正计划，归档文件到交付目录
- **撤销**：回滚已应用的修正操作
- **导出报告**：生成 JSON/CSV 格式的校验报告

## 快速开始

```bash
# 1. 安装
pip install -e .

# 2. 生成样例数据
photo-archive init-sample ./sample_data

# 3. 使用样例配置
cd ./sample_data

# 4. 扫描存储卡
photo-archive -c config.yaml scan ./source_cards

# 5. 导入交付清单
photo-archive -c config.yaml import-list ./delivery_list/delivery_manifest.csv

# 6. 校验
photo-archive -c config.yaml verify

# 7. 生成修正计划
photo-archive -c config.yaml plan

# 8. 应用修正
photo-archive -c config.yaml apply

# 9. 导出报告
photo-archive -c config.yaml report -o report.json --format json
```

## 配置文件 (config.yaml)

```yaml
naming_rule: "{机位}_{批次}_{序号:04d}.{扩展名}"
cameras:
  - A
  - B
  - C
hash_strategy: sha256
archive_dir: ./archive
work_dir: ./work
```

## 非零退出码说明

| 退出码 | 说明 |
|--------|------|
| **0** | 成功 |
| **1** | 通用错误（命令执行失败） |
| **2** | 清单错误（损坏或包含重复文件名） |
| **3** | 哈希不一致（扫描后文件被篡改或期望哈希不匹配） |
| **4** | 重复文件名（源目录或清单中存在重复） |
| **5** | 缺片（清单中存在但源目录中缺失的文件） |
| **6** | 没有可撤销的操作 |

也可以通过命令查看：
```bash
photo-archive help-exit-codes
```

## 命令列表

| 命令 | 说明 |
|------|------|
| `init-sample` | 初始化样例数据 |
| `scan` | 扫描源目录中的照片文件 |
| `import-list` | 导入交付清单 (CSV 格式) |
| `verify` | 校验交付清单与源文件的匹配情况 |
| `plan` | 生成修正计划 |
| `apply` | 应用修正计划 |
| `undo` | 撤销已应用的修正 |
| `report` | 导出校验报告 |
| `list-batches` | 列出所有批次 |
| `help-exit-codes` | 显示非零退出码说明 |

## 报告内容

报告包含以下部分：
- **文件映射**：原路径 → 目标文件名
- **缺片列表**：清单中存在但源目录中缺失的文件
- **重复文件**：源目录中存在多个同名文件
- **哈希变化**：实际哈希与期望不一致的文件
- **实际执行的修正**：所有已应用的操作记录

## 项目结构

```
photo_archive/
├── cli.py              # CLI 主入口
├── config.py           # 配置管理
├── models.py           # 数据模型
├── storage.py          # 批次历史持久化
├── scanner.py          # 文件扫描和哈希计算
└── commands/
    ├── init_sample.py  # 初始化样例
    ├── scan.py         # 扫描目录
    ├── import_list.py  # 导入交付清单
    ├── verify.py       # 校验
    ├── plan.py         # 生成修正计划
    ├── apply.py        # 应用修正
    ├── undo.py         # 撤销
    └── report.py       # 导出报告
```

## 修复的关键漏洞

### 1. 命名规则字段匹配

**问题**：源卡原始文件名（如 `A_0001.jpg`）和交付清单目标名（如 `A_WEDDING_0001.jpg`）不一致时，会误报为缺片。

**修复**：配置中的 `naming_rule` 参与扫描结果和清单的匹配，通过解析文件名中的机位、序号、扩展名等字段进行智能匹配。

### 2. Apply 前哈希重校验

**问题**：扫描后文件被篡改，apply 时仍会把脏文件归档成正常批次。

**修复**：apply 执行前重新校验源文件当前哈希；如果扫描后文件被改写，阻止复制、返回退出码 3，并在报告中记录 `hash_mismatch`。
