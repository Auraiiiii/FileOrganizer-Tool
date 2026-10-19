# FileOrganizer-Tool

<div align="center">

**把混乱变成秩序，只需一次点击。**

一款基于 Python + Tkinter 的本地文件整理工具：按类型归档、按修改月份归档，并为每一次整理保留一条安全的“后悔路”。

![Python](https://img.shields.io/badge/Python-3.10%2B-3776AB?logo=python&logoColor=white)
![Tkinter](https://img.shields.io/badge/GUI-Tkinter-3776AB?logo=python&logoColor=white)
![Platform](https://img.shields.io/badge/Platform-Windows%20%7C%20macOS%20%7C%20Linux-lightgrey)
![Dependencies](https://img.shields.io/badge/Dependencies-Standard%20Library-success)
![Status](https://img.shields.io/badge/Status-Stable-brightgreen)
![PRs Welcome](https://img.shields.io/badge/PRs-welcome-brightgreen)

</div>

> **FileOrganizer-Tool** 不是又一个把文件挪来挪去的脚本。它把分类规则、后台执行、冲突避让和可逆操作放进同一个轻量桌面界面里——整理之前看得见，整理之后退得回。

---

## 界面预览

> **截图占位**
>
> ![软件截图]（https://raw.githubusercontent.com/Auraiiiii/FileOrganizer-Tool/f9864ec7ebd8780b7db1d90d2f345c8754a8a1f0/%E5%BE%AE%E4%BF%A1%E5%9B%BE%E7%89%87_20261019174719_4_1.png?token=CRYJ6B23GNOY37H2FFC6M63K2XUPM）
>
> 可使用 Markdown 图片语法：`![FileOrganizer-Tool 界面截图](docs/images/screenshot.png)`

---

## 目录

- [界面预览](#界面预览)
- [核心功能](#核心功能)
- [设计原则](#设计原则)
- [快速开始](#快速开始)
- [使用指南](#使用指南)
- [项目结构](#项目结构)
- [常见问题](#常见问题)
- [贡献指南](#贡献指南)
- [License](#license)

## 核心功能

### 按文件类型分类

根据文件扩展名自动创建分类文件夹，并移动所选文件夹顶层的文件。

| 目标文件夹 | 支持的扩展名 |
| :--- | :--- |
| `Images` | `.jpg`、`.jpeg`、`.png` |
| `Documents` | `.docx`、`.pdf`、`.txt` |
| `Videos` | `.mp4` |
| `Archives` | `.zip`、`.rar` |
| `Others` | 其他未匹配的扩展名 |

整理后的目录示例：

```text
Downloads/
├── Images/
│   ├── cover.png
│   └── photo.jpg
├── Documents/
│   ├── report.pdf
│   └── notes.txt
└── Archives/
    └── assets.zip
```

> 只处理所选文件夹顶层的文件，不会递归进入已有的子文件夹。

### 按修改时间分类

选择“按修改时间分类”后，程序会读取文件的最后修改时间（`mtime`），按系统本地时区创建 `YYYY-MM` 格式的月份文件夹。

```text
Downloads/
├── 2026-09/
│   ├── old-report.pdf
│   └── summer-photo.jpg
└── 2026-10/
    ├── invoice.pdf
    └── design-assets.zip
```

这种方式适合按月份归档截图、账单、导出文件、项目交付物等需要保留时间脉络的资料。

### 一键撤销

每次整理成功后，程序都会记录成功移动文件的**原始路径**和**目标路径**。

点击界面底部的“撤销上一步”按钮，程序会在后台线程中按相反顺序把文件移回原位，并在日志区输出结果。

撤销流程具备以下容错能力：

- 目标文件已经被手动删除或替换时，跳过该文件并在日志中提示。
- 原始位置已经存在同名文件时，跳过该文件，避免覆盖用户的新文件。
- 单个文件恢复失败不会中断整个撤销流程。
- 部分文件未恢复时，界面会弹出警告并显示完整统计。

> 撤销记录保存在当前程序会话的内存中，对应最近一次实际移动过文件的整理操作。关闭程序后记录会失效；撤销也不会自动删除整理时留下的空文件夹。

## 设计原则

### 本地优先

所有文件操作都在用户选择的本地目录中完成。程序不上传文件、不依赖云端服务，也不需要额外数据库、网络接口或常驻进程。

### 不覆盖用户数据

目标位置存在同名文件时，程序会自动生成 `文件名 (1).扩展名` 形式的新名称，避免静默覆盖。

### 可逆操作

文件移动不是单向操作。每次成功移动都会生成撤销记录，让整理行为可以被安全回退。

### 界面不阻塞

文件整理和撤销均在后台线程中执行，界面通过线程安全队列接收日志和结果，不会因为大量文件移动而卡死。

### 程序文件保护

整理时会自动跳过正在运行的 `gui_organizer.py` 和同目录下的 `organizer.py`，避免把程序自身移入分类文件夹。

## 快速开始

### 环境要求

- Python 3.10 或更高版本
- Tkinter（Python 官方安装包默认包含）
- Windows、macOS 或 Linux

项目只使用 Python 标准库，不需要安装第三方依赖。

### 1. 安装 Python

前往 [Python 官方网站](https://www.python.org/downloads/) 下载并安装 Python 3.10 或更高版本。

Windows 安装时请勾选：

```text
Add python.exe to PATH
```

安装完成后，打开终端或 PowerShell 检查版本：

```bash
python --version
```

如果系统中同时存在多个 Python 版本，也可以使用 Windows 自带的启动器：

```powershell
py --version
```

### 2. 检查 Tkinter

Windows 和 macOS 的官方 Python 安装包通常已经包含 Tkinter。可以运行以下命令确认：

```bash
python -c "import tkinter; print('Tkinter OK')"
```

如果 Linux 系统提示找不到 `tkinter`，请安装对应发行版的 Tk 包：

```bash
# Debian / Ubuntu
sudo apt update
sudo apt install python3-tk

# Fedora
sudo dnf install python3-tkinter

# Arch Linux
sudo pacman -S tk
```

### 3. 获取项目

将项目下载到本地并进入项目目录：

```bash
git clone <repository-url>
cd FileOrganizer-Tool
```

如果你下载的是压缩包，解压后进入 `FileOrganizer-Tool` 目录即可。

### 4. 运行图形界面

Windows 推荐使用：

```powershell
py gui_organizer.py
```

macOS、Linux 或已配置 Python 环境变量的 Windows 用户可以使用：

```bash
python gui_organizer.py
```

也可以直接双击 `gui_organizer.py` 文件运行。如果系统没有把 `.py` 文件关联到 Python，可以右键选择“打开方式”，或使用上面的命令行方式启动。

## 使用指南

1. 点击“浏览…”选择需要整理的文件夹。
2. 在“整理方式”下拉菜单中选择“按文件类型分类”或“按修改时间分类”。
3. 点击“开始整理”，等待后台任务完成。
4. 查看日志区中的移动记录和统计结果。
5. 如果需要恢复，点击界面底部的“撤销上一步”。

> 建议在首次使用前备份重要文件。虽然程序会避让同名文件并提供撤销能力，但文件移动仍然是真实的磁盘操作。

## 项目结构

```text
FileOrganizer-Tool/
├── gui_organizer.py   # 图形界面、整理模式、撤销逻辑
├── organizer.py       # 早期命令行版本
└── README.md          # 项目说明
```

`organizer.py` 是保留的命令行入口，默认整理当前工作目录，功能少于图形界面版本。日常使用建议运行 `gui_organizer.py`。

## 常见问题

### 运行提示 `python` 不是内部或外部命令

说明 Python 没有加入系统 `PATH`。可以尝试使用：

```powershell
py gui_organizer.py
```

或者在安装 Python 时重新勾选 `Add python.exe to PATH`。

### 提示 `No module named 'tkinter'`

当前 Python 环境没有安装 Tkinter。Windows 和 macOS 用户建议使用 [python.org](https://www.python.org/downloads/) 提供的官方安装包；Linux 用户请参考上文的发行版安装命令。

### 为什么子文件夹里的文件没有被整理？

程序只处理所选文件夹顶层的文件，不会递归进入子文件夹。这是为了避免误移动项目目录、软件目录中的深层文件。

### 为什么撤销按钮是灰色的？

只有在本次程序运行期间成功移动过文件后，撤销按钮才会启用。关闭程序后，内存中的撤销记录会被清空。

### 撤销时提示文件被跳过

通常有两种原因：

- 整理后的文件已经被手动删除或替换。
- 原始位置已经出现了同名文件，程序为避免覆盖而主动跳过。

具体原因可以在日志区查看。

## 贡献指南

欢迎提交 Issue 或 Pull Request，一起改进分类规则、界面体验和安全性。

建议流程：

1. Fork 本仓库。
2. 创建功能分支：`git checkout -b feature/your-feature`。
3. 提交修改并补充必要说明。
4. 发起 Pull Request，描述改动内容和验证方式。

## License

当前仓库尚未附带开源许可证文件。正式公开发布前，建议根据团队或项目规范补充 `LICENSE` 文件。

---

<div align="center">

**Built with Python and Tkinter.**

让文件回到该在的位置。

</div>
