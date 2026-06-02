# ⚡ Excel 快捷查询助手 · Excel Search Helper

> 常驻系统托盘，全局快捷键一键从 Excel/SQLite/PostgreSQL/向量数据库查询并回写  
> Resident in system tray — hotkey-triggered query & write-back across Excel, SQLite, PostgreSQL, and vector databases.

[English Documentation](README.md) | [中文说明](README_ZH.md)

---

## 📚 文档目录 · Documentation

| 文件 | 说明 | Description |
|:---|:---|:---|
| [README.md](documents/README.md) | 中文完整说明（安装、配置、扩展开发） | Full guide in Chinese |
| [README_EN.md](documents/README_EN.md) | English full guide | Full guide in English |
| [新手极速入门与配置指南.md](documents/新手极速入门与配置指南.md) | 中文新手手把手教程 | Chinese beginner tutorial |
| [BEGINNER_GUIDE.md](documents/BEGINNER_GUIDE.md) | English beginner tutorial | English beginner tutorial |
| [CONTRIBUTING.md](documents/CONTRIBUTING.md) | 插件开发与贡献指南 | Plugin development & contribution guide |
| [开源可行性分析.md](documents/开源可行性分析.md) | 项目架构通用性分析 | Architecture & generality analysis |

---

## 🚀 快速开始 · Quick Start

```powershell
# 1. 克隆仓库 / Clone
git clone https://github.com/your-username/excel-search-helper.git
cd excel-search-helper

# 2. 安装核心依赖（仅 7 个包）/ Install core dependencies
pip install -r requirements-core.txt

# 3. 生成演示测试数据 / Generate demo data
python test/generate_test_data.py

# 4. 复制配置模板并按需修改 / Copy config template
copy config.example.yaml config.yaml

# 5. 启动 / Run
python main.py
```

> **向量检索扩展**（可选）：`pip install -r requirements-vector.txt`

---

## ✨ 核心特性 · Features

- 🔑 **全局热键唤起** — 在任意应用中按下自定义快捷键，毫秒级弹出查询面板
- 🔌 **多数据源插件化** — Excel 文件 / SQLite / PostgreSQL / Qdrant 向量库，一行配置切换
- 🖊️ **双击回写 & 批量填充** — 双击结果行自动回写 Excel，支持框选多行批量处理
- ⚙️ **全图形化设置** — 无需手写 YAML，图形界面完成所有配置
- 🌐 **中英文自动切换** — 跟随操作系统语言，也可手动切换

---

## 📦 依赖说明 · Dependencies

| 文件 | 用途 |
|:---|:---|
| `requirements-core.txt` | 核心依赖，仅 7 个包，适合 Excel/SQLite 用户 |
| `requirements-vector.txt` | 向量检索扩展，安装 Qdrant 客户端等 |
| `requirements.txt` | 完整依赖清单（含所有版本锁定，用于开发/打包） |

---

## 📄 许可证 · License

本项目遵循 [GPLv3](LICENSE) 开源许可协议。
