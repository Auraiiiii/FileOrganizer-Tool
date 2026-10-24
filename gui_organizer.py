# -*- coding: utf-8 -*-
"""文件整理助手（图形界面版）。

这个程序使用 customtkinter 构建轻量、现代的桌面图形界面。
它保留下面的整理逻辑：
1. 只整理所选文件夹顶层的文件，不会递归进入子文件夹。
2. 支持按扩展名分类，以及按文件修改月份归档两种整理方式。
3. 如果目标文件夹中有同名文件，会自动生成“文件名 (1).扩展名”这样的新名称。
4. 每次整理都会记录文件的原始路径和目标路径，支持一键撤销最近一次整理。
"""

# 延迟解析类型注解，让较旧版本的 Python 也能正常读取这个文件。
from __future__ import annotations

# 导入 json 模块，把界面偏好保存为可读的本地配置文件。
import json
# 导入 os 模块，用原子替换避免程序中断时留下半个 config.json。
import os
# 导入队列模块，用于让后台整理线程把日志安全地传给界面线程。
import queue
# 导入 shutil 模块，用来移动文件。
import shutil
# 导入 sys，用于区分源码运行和 PyInstaller 单文件运行。
import sys
# 导入 threading 模块，让文件整理和撤销都在后台运行，避免窗口卡住。
import threading
# 导入 tkinter，它是 Python 自带的图形界面库。
import tkinter as tk
# 导入 customtkinter，用圆角卡片和现代控件替换传统原生外观。
import customtkinter as ctk
# 导入 tkinterdnd2，用系统原生拖拽能力接收资源管理器中的文件夹。
from tkinterdnd2 import COPY, DND_FILES, TkinterDnD
# 从 dataclasses 中导入 dataclass，用来保存每一次文件移动记录。
from dataclasses import dataclass
# 从 datetime 中导入 datetime，用来生成月份文件夹名称和日志时间。
from datetime import datetime
# 从 pathlib 中导入 Path，用来更方便地处理文件路径。
from pathlib import Path
# 从 tkinter 中导入文件选择窗口和消息框。
from tkinter import filedialog, messagebox


# 定义分类规则：字典的键是文件夹名称，值是允许放入该文件夹的扩展名集合。
CATEGORY_RULES = {
    # 图片文件统一移动到 Images 文件夹。
    "Images": {".jpg", ".jpeg", ".png"},
    # 文档文件统一移动到 Documents 文件夹。
    "Documents": {".docx", ".pdf", ".txt"},
    # 视频文件统一移动到 Videos 文件夹。
    "Videos": {".mp4"},
    # 压缩包统一移动到 Archives 文件夹。
    "Archives": {".zip", ".rar"},
}

# 自定义分流沿用了默认分类的扩展名规则，避免两种入口产生不同判断。
# 每项由界面显示文字和 CATEGORY_RULES 中的内部分类名称组成。
DIVERSION_OPTIONS = (
    ("图片", "Images"),
    ("文档", "Documents"),
    ("视频", "Videos"),
    ("压缩包", "Archives"),
)

# 反向映射便于根据下拉菜单当前文字查找内部分类名称。
DIVERSION_CATEGORY_LABELS = {
    category: label for label, category in DIVERSION_OPTIONS
}


# 定义两种整理模式的内部名称，避免界面文字和业务逻辑直接耦合。
MODE_BY_TYPE = "type"
MODE_BY_TIME = "time"

# 定义下拉菜单中显示的选项；每一项由“显示文字”和“内部模式名称”组成。
MODE_OPTIONS = (
    ("按文件类型分类", MODE_BY_TYPE),
    ("按修改时间分类", MODE_BY_TIME),
)

# 只有这两种纯文本格式适合把内容发送给摘要模型。
AI_RENAME_SUFFIXES = {".txt", ".md"}
# 按需求只读取每个文档最前面的 500 个字符，控制隐私范围和请求大小。
AI_TEXT_PREVIEW_LENGTH = 500
# 即使模型没有严格遵守提示词，也用这个上限控制最终文件名长度。
AI_SUMMARY_MAX_CHARS = 10
# Windows 文件名不允许这些字符；其他系统也可能因路径规则拒绝它们。
INVALID_FILE_NAME_CHARS = frozenset('<>:"/\\|?*')
# API Key 配置与 AI 模块使用同一个 .env 文件。
ENV_FILE = Path(__file__).resolve().with_name(".env")
# .env 中保存 DeepSeek API Key 的变量名。
API_KEY_ENV_NAME = "DEEPSEEK_API_KEY"
# 界面中用于隐藏已保存 Key 的占位文字。
API_KEY_MASK = "********"
# 单文件打包后程序目录是 exe 所在位置，而不是会被清理的 PyInstaller 临时目录。
APPLICATION_DIRECTORY = (
    Path(sys.executable).resolve().parent
    if getattr(sys, "frozen", False)
    else Path(__file__).resolve().parent
)
# 配置与程序放在同一目录，便于绿色版和源码版使用相同的恢复行为。
CONFIG_FILE = APPLICATION_DIRECTORY / "config.json"
# 配置格式版本便于以后升级字段时做兼容迁移。
CONFIG_VERSION = 1


def env_has_api_key(env_file: Path = ENV_FILE) -> bool:
    """检查 .env 中是否配置了非空的 DEEPSEEK_API_KEY。"""

    try:
        env_lines = env_file.read_text(encoding="utf-8-sig").splitlines()
    except OSError:
        # 文件不存在或无法读取时按未配置处理，不把异常带到启动界面。
        return False

    for line in env_lines:
        stripped_line = line.strip()
        if not stripped_line or stripped_line.startswith("#"):
            continue
        if stripped_line.startswith("export "):
            stripped_line = stripped_line[len("export ") :].lstrip()
        variable_name, separator, value = stripped_line.partition("=")
        if separator and variable_name.strip() == API_KEY_ENV_NAME:
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
                value = value[1:-1].strip()
            return bool(value)
    return False


def write_api_key_to_env(api_key: str, env_file: Path = ENV_FILE) -> None:
    """把 API Key 写入 .env，同时保留文件中的其他配置项。"""

    if "\n" in api_key or "\r" in api_key:
        raise ValueError("API Key 不能包含换行符")

    try:
        old_lines = env_file.read_text(encoding="utf-8-sig").splitlines()
    except FileNotFoundError:
        old_lines = []

    updated_lines: list[str] = []
    key_replaced = False
    for line in old_lines:
        stripped_line = line.strip()
        assignment_line = (
            stripped_line[len("export ") :].lstrip()
            if stripped_line.startswith("export ")
            else stripped_line
        )
        variable_name, separator, _ = assignment_line.partition("=")
        if (
            not stripped_line.startswith("#")
            and separator
            and variable_name.strip() == API_KEY_ENV_NAME
        ):
            # 如果 .env 中已有重复配置，只保留一条最新的 Key。
            if not key_replaced:
                updated_lines.append(f"{API_KEY_ENV_NAME}={api_key}")
                key_replaced = True
            continue
        updated_lines.append(line)

    if not key_replaced:
        updated_lines.append(f"{API_KEY_ENV_NAME}={api_key}")

    env_file.write_text("\n".join(updated_lines).rstrip("\n") + "\n", encoding="utf-8")


def describe_exception(error: Exception) -> str:
    """把异常转换成适合日志区显示的具体错误信息。"""

    # 对 OpenAI API 错误保留 HTTP 状态码，方便判断鉴权、余额或服务端问题。
    status_code = getattr(error, "status_code", None)
    error_detail = str(error).strip() or repr(error)
    if status_code is not None:
        return f"{type(error).__name__}（HTTP {status_code}）：{error_detail}"
    return f"{type(error).__name__}：{error_detail}"


@dataclass(frozen=True)
class MoveRecord:
    """记录一次成功移动，撤销时按相反顺序把文件移回原位置。"""

    # 保存文件整理前的原始路径。
    original_path: Path
    # 保存文件整理后的目标路径。
    destination_path: Path


@dataclass(frozen=True)
class DiversionRule:
    """一条“文件类型 -> 目标文件夹”的分流规则。"""

    category: str
    target_directory: Path


def normalize_diversion_rules(raw_rules: object) -> list[DiversionRule]:
    """从配置或界面数据中提取有效分流规则。

    无效类型和空目标会被忽略；不要求目标此刻存在，这样网络盘暂时离线时，
    规则仍能恢复到界面，并在真正整理时给出明确失败原因。
    """

    if not isinstance(raw_rules, list):
        return []

    valid_categories = {category for _, category in DIVERSION_OPTIONS}
    category_by_label = {
        label: category for label, category in DIVERSION_OPTIONS
    }
    normalized_rules: list[DiversionRule] = []
    for item in raw_rules:
        if not isinstance(item, dict):
            continue

        raw_category = item.get("category", item.get("type"))
        if not isinstance(raw_category, str):
            continue
        category = category_by_label.get(raw_category, raw_category)
        if category not in valid_categories:
            continue

        raw_target = item.get(
            "target_directory",
            item.get("target", item.get("path")),
        )
        if not isinstance(raw_target, str):
            continue
        target_text = raw_target.strip().strip('"')
        if not target_text:
            continue

        normalized_rules.append(
            DiversionRule(
                category=category,
                target_directory=Path(target_text).expanduser(),
            )
        )
    return normalized_rules


def read_app_config(
    config_file: Path = CONFIG_FILE,
) -> tuple[dict, str]:
    """读取配置；缺失时返回空配置，损坏时返回可展示的警告。"""

    try:
        raw_config = json.loads(config_file.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}, ""
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        return {}, f"读取配置失败，已使用默认设置：{describe_exception(error)}"

    if not isinstance(raw_config, dict):
        return {}, "配置文件格式无效，已使用默认设置。"
    return raw_config, ""


def write_app_config(
    config: dict,
    config_file: Path = CONFIG_FILE,
) -> None:
    """以 UTF-8 JSON 原子写入配置，避免异常退出损坏原文件。"""

    config_file.parent.mkdir(parents=True, exist_ok=True)
    temporary_file = config_file.with_name(f".{config_file.name}.tmp")
    try:
        with temporary_file.open("w", encoding="utf-8", newline="\n") as file:
            json.dump(config, file, ensure_ascii=False, indent=2)
            file.write("\n")
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary_file, config_file)
    except Exception:
        try:
            temporary_file.unlink(missing_ok=True)
        except OSError:
            pass
        raise


# 定义函数，根据文件扩展名返回文件所属的分类名称。
def get_category(file_path: Path) -> str:
    # 获取文件扩展名并转换为小写，让 .JPG 和 .jpg 都能被正确识别。
    suffix = file_path.suffix.lower()
    # 依次检查分类规则中的每个分类。
    for category, extensions in CATEGORY_RULES.items():
        # 如果扩展名属于当前分类，就返回对应的文件夹名称。
        if suffix in extensions:
            return category
    # 所有分类都不匹配时，返回 Others，表示未知格式。
    return "Others"


# 定义函数，根据文件的最后修改时间返回“年-月”格式的分类名称。
def get_month_category(file_path: Path) -> str:
    # 读取文件的最后修改时间；读取失败时由调用方统一捕获并记录。
    modified_time = file_path.stat().st_mtime
    # 把时间戳转换为本地时间，再格式化成类似 2026-10 的文件夹名称。
    return datetime.fromtimestamp(modified_time).strftime("%Y-%m")


# 定义函数，为目标文件生成一个不会覆盖已有文件的路径。
def build_destination(target_directory: Path, file_name: str) -> Path:
    # 先拼接出最直接的目标路径。
    destination = target_directory / file_name
    # 如果目标位置没有同名文件，就直接使用这个路径。
    if not destination.exists():
        return destination

    # 保存原文件名中除扩展名以外的部分。
    stem = destination.stem
    # 保存原文件的扩展名。
    suffix = destination.suffix
    # 从数字 1 开始尝试添加序号。
    index = 1
    # 不断尝试新名称，直到找到不存在的文件名。
    while True:
        # 生成类似“文件名 (1).jpg”的候选路径。
        candidate = destination.with_name(f"{stem} ({index}){suffix}")
        # 如果候选路径没有被占用，就可以安全使用。
        if not candidate.exists():
            return candidate
        # 当前序号已占用，准备检查下一个序号。
        index += 1


# 定义函数，把模型摘要转换成安全、简短且不会覆盖文件的名称。
def build_ai_file_name(summary: str, original_path: Path) -> str:
    """根据摘要生成 AI 重命名后的文件名。

    摘要由远程模型生成，不能直接拼进路径。这里会合并空白、限制长度，
    并把 Windows 不允许的字符替换成下划线，防止 create/rename 时崩溃。
    """

    # 合并换行和连续空格，避免摘要中包含不可控的多行文件名。
    compact_summary = " ".join(summary.split()).strip()
    # 逐字符过滤控制字符和文件系统保留字符。
    safe_chars = []
    for character in compact_summary:
        if character in INVALID_FILE_NAME_CHARS or ord(character) < 32:
            safe_chars.append("_")
        else:
            safe_chars.append(character)
    # 去掉摘要首尾可能留下的空格、点和下划线。
    safe_summary = "".join(safe_chars).strip(" ._")
    # 模型偶尔会超过提示词要求的长度，程序侧再次截断以保证安全。
    safe_summary = safe_summary[:AI_SUMMARY_MAX_CHARS].rstrip(" ._")

    # 空摘要无法形成有意义的新文件名，通知调用方回退到原整理流程。
    if not safe_summary:
        raise ValueError("AI 摘要中没有可用于文件名的有效字符")

    # 按需求保留原文件主名称，并统一生成 .txt 后缀。
    # 例如 notes.md 和“项目计划”会变成 [项目计划]_notes.txt。
    return f"[{safe_summary}]_{original_path.stem}.txt"


# 定义函数，安全读取文档开头的一小段文本供 AI 总结。
def read_ai_preview(file_path: Path) -> str:
    """读取文本文件的前 500 个字符，无法解码的字节使用替换字符。"""

    # 使用 utf-8-sig 兼容带 BOM 的文本文件；errors="replace" 让少量坏字符
    # 不会阻止整个文件继续处理。read(500) 统计的是 Python 字符串字符数。
    with file_path.open(
        "r",
        encoding="utf-8-sig",
        errors="replace",
    ) as source_file:
        return source_file.read(AI_TEXT_PREVIEW_LENGTH)


# 定义整理函数，接收要整理的文件夹路径、整理模式和日志回调函数。
def organize_directory(
    source_directory: Path,
    skip_paths: set[Path] | None = None,
    log_callback=None,
    mode: str = MODE_BY_TYPE,
    move_records: list[MoveRecord] | None = None,
    use_ai: bool = False,
    diversion_rules: list[DiversionRule] | None = None,
) -> tuple[int, int]:
    """整理指定文件夹，并返回（成功数量，失败数量）。

    source_directory：要整理的文件夹。
    skip_paths：整理时需要跳过的文件路径，例如正在运行的程序本身。
    log_callback：接收日志文本的函数；传入 None 时不输出过程信息。
    mode：整理模式，MODE_BY_TYPE 表示按类型，MODE_BY_TIME 表示按修改月份。
    move_records：用于收集成功移动记录的列表，供“撤销上一步”使用。
    use_ai：为 True 时尝试为 .txt/.md 文件生成 AI 摘要并重命名。
    diversion_rules：按顺序匹配的“文件类型 -> 目标文件夹”规则列表。
    """

    # 如果没有提供日志函数，就使用一个什么都不做的小函数。
    if log_callback is None:
        log_callback = lambda message: None

    # 检查整理模式是否有效，避免因为参数写错而把文件移动到意外位置。
    if mode not in {MODE_BY_TYPE, MODE_BY_TIME}:
        raise ValueError(f"未知的整理模式：{mode}")

    # 如果没有传入记录列表，就创建一个临时列表，保证函数仍然可以独立使用。
    if move_records is None:
        move_records = []

    # 逐条校验规则；无效目标只影响对应类型，其他规则和默认整理仍可继续。
    # 同类型规则按界面顺序取第一条，避免文件目标依赖偶然的字典顺序。
    validated_rules: list[DiversionRule] = []
    configured_categories: set[str] = set()
    source_resolved = source_directory.resolve()
    for rule in diversion_rules or []:
        category_label = DIVERSION_CATEGORY_LABELS.get(
            rule.category,
            "未知类型",
        )
        if rule.category not in DIVERSION_CATEGORY_LABELS:
            log_callback(
                f"[分流] 忽略无效类型：{category_label}。"
            )
            continue
        if rule.category in configured_categories:
            log_callback(
                f"[分流] {category_label}存在多条规则，"
                "按列表顺序使用第一条。"
            )
            continue

        target_directory = Path(rule.target_directory).expanduser()
        if not target_directory.exists():
            log_callback(
                f"[分流] {category_label}的目标文件夹不存在："
                f"{target_directory}，该类型将按默认方式整理。"
            )
            continue
        if not target_directory.is_dir():
            log_callback(
                f"[分流] {category_label}的目标路径不是文件夹："
                f"{target_directory}，该类型将按默认方式整理。"
            )
            continue

        try:
            target_directory = target_directory.resolve()
        except OSError as error:
            log_callback(
                f"[分流] 无法解析{category_label}的目标文件夹："
                f"{describe_exception(error)}，该类型将按默认方式整理。"
            )
            continue
        if target_directory == source_resolved:
            log_callback(
                f"[分流] {category_label}的目标不能与工作目录相同，"
                "该类型将按默认方式整理。"
            )
            continue

        configured_categories.add(rule.category)
        validated_rules.append(
            DiversionRule(
                category=rule.category,
                target_directory=target_directory,
            )
        )
        log_callback(
            f"[分流] 已启用：{category_label} -> {target_directory}"
        )

    # 保存需要跳过的文件路径，统一转换成绝对路径，方便比较。
    protected_paths = {path.resolve() for path in (skip_paths or set())}
    # 初始化成功移动的文件数量。
    moved_count = 0
    # 初始化移动失败的文件数量。
    failed_count = 0
    # 初始化找到的可整理文件数量。
    found_count = 0

    # 下面的计数会写入日志，确保用户能确认 AI 是否真正执行过。
    ai_eligible_count = 0
    ai_attempted_count = 0
    ai_success_count = 0
    ai_failure_count = 0

    # AI 模块采用延迟导入：只有用户勾选 AI 功能时才加载第三方依赖。
    summarize_text = None
    ai_unavailable_reason = ""
    if use_ai:
        try:
            from ai_renamer import summarize_text as imported_summarize_text
            import ai_renamer

            # 在整理开始前重新读取当前配置，明确区分“缺少 Key”和依赖问题。
            ai_renamer.reload_ai_configuration()
            if not ai_renamer.is_api_key_configured():
                ai_unavailable_reason = "请先配置 API Key"
                log_callback("请先配置 API Key")
            else:
                summarize_text = imported_summarize_text
            # 每次启用 AI 时都打印配置诊断，明确实际读取的 .env 路径、
            # Key 是否存在以及依赖和客户端的状态，且不会泄露密钥内容。
            log_callback(f"AI 配置诊断：{ai_renamer.get_ai_diagnostics()}")
        except Exception as error:
            # 模块导入或诊断初始化失败时保留完整异常类型和消息。
            ai_unavailable_reason = describe_exception(error)
            log_callback(
                f"AI 初始化失败：{ai_unavailable_reason}。"
                "本次将按普通方式整理文件。"
            )

    # 先复制一份顶层文件列表再遍历；AI 重命名会改变目录项名称，
    # 使用快照可以避免边遍历边修改目录造成漏处理文件。
    for file_path in list(source_directory.iterdir()):
        # 如果当前项目不是文件，就跳过文件夹、快捷方式等非普通文件。
        if not file_path.is_file():
            continue

        # 读取文件的绝对路径；个别特殊文件可能无法读取，此时直接跳过。
        try:
            resolved_path = file_path.resolve()
        except OSError as error:
            log_callback(f"跳过无法读取的文件：{file_path.name}（{error}）")
            continue

        # 跳过程序文件本身，避免整理时把正在运行的脚本移走。
        if resolved_path in protected_paths:
            log_callback(f"跳过程序文件：{file_path.name}")
            continue

        # 找到一个需要整理的文件。
        found_count += 1

        # 使用异常处理，避免单个文件失败后中断整个整理任务。
        try:
            # current_path 会随着 AI 重命名而变化；API 失败时仍指向原文件。
            current_path = file_path

            # 判断当前文件是否属于 AI 允许处理的纯文本格式。
            is_ai_document = (
                current_path.suffix.lower() in AI_RENAME_SUFFIXES
            )

            # 仅对勾选 AI 时的 .txt 和 .md 文档执行摘要与重命名。
            if use_ai and is_ai_document:
                ai_eligible_count += 1

                # 模块不可用也必须为每个符合条件的文件写明原因。
                if summarize_text is None:
                    ai_failure_count += 1
                    reason = ai_unavailable_reason or "AI 模块未成功加载"
                    log_callback(
                        f"AI 重命名跳过：{file_path.name}。"
                        f"具体原因：{reason}。文件将按普通方式整理。"
                    )
                else:
                    ai_attempted_count += 1
                    try:
                        # 只读取前 500 个字符，避免读取大文件或发送过多隐私内容。
                        preview = read_ai_preview(current_path)
                        # 网络请求在后台整理线程中执行，因此不会冻结 tkinter 界面。
                        summary = summarize_text(preview)
                        # 把模型摘要清洗成安全的文件名。
                        ai_file_name = build_ai_file_name(summary, current_path)
                        # 在原文件夹中处理重名冲突，确保不会覆盖已有文件。
                        ai_renamed_path = build_destination(
                            source_directory,
                            ai_file_name,
                        )
                        # 按需求先在原位置重命名，再进入统一的分类移动流程。
                        current_path.rename(ai_renamed_path)
                        # 保留用户要求的重命名日志格式。
                        log_callback(
                            f"AI 已重命名: {file_path.name} -> "
                            f"{ai_renamed_path.name}"
                        )
                        # 后续分类和移动都基于 AI 重命名后的实际路径。
                        current_path = ai_renamed_path
                        ai_success_count += 1
                    except Exception as error:
                        # 鉴权失败、余额不足、无网络、超时、空摘要等都在这里记录。
                        # 错误日志包含异常类型、消息和 HTTP 状态码，然后明确回退。
                        ai_failure_count += 1
                        reason = describe_exception(error)
                        log_callback(
                            f"AI 重命名失败：{file_path.name}。"
                            f"具体原因：{reason}。文件将按普通方式整理。"
                        )
                        current_path = file_path

            # 按列表顺序查找首个同类型规则；未命中时继续原有分类方式。
            current_category = get_category(current_path)
            matched_rule = next(
                (
                    rule
                    for rule in validated_rules
                    if rule.category == current_category
                ),
                None,
            )
            if matched_rule is not None:
                target_directory = matched_rule.target_directory
                category = DIVERSION_CATEGORY_LABELS[matched_rule.category]
            else:
                # 根据用户选择的模式决定分类文件夹名称。
                if mode == MODE_BY_TIME:
                    category = get_month_category(current_path)
                else:
                    category = get_category(current_path)

                # 拼接默认分类文件夹的完整路径。
                target_directory = source_directory / category
                # 创建分类文件夹；如果文件夹已经存在，不会报错。
                target_directory.mkdir(parents=True, exist_ok=True)

            # 计算不会覆盖已有文件的目标路径。
            destination = build_destination(target_directory, current_path.name)
            # 把当前文件从原位置移动到分类文件夹中。
            shutil.move(str(current_path), str(destination))
            # 只有移动成功后，才保存撤销所需的原始路径和目标路径。
            move_records.append(
                MoveRecord(
                    original_path=file_path.absolute(),
                    destination_path=destination.absolute(),
                )
            )
            # 分流日志明确显示完整目标路径；普通分类保持原有简洁格式。
            if matched_rule is not None:
                log_callback(
                    f"[分流] 已移动: {current_path.name} -> {destination}"
                )
            else:
                log_callback(f"已移动：{current_path.name} -> {category}/")
            # 成功移动后，把成功数量加一。
            moved_count += 1
        # 权限不足是跨盘和系统目录移动中最常见的问题，单独给出明确提示。
        except PermissionError as error:
            log_callback(
                f"移动失败（权限不足）：{current_path.name}。"
                f"请检查源文件和目标文件夹的写入权限（{error}）"
            )
            failed_count += 1
        # 捕获文件移动过程中可能出现的系统或权限错误。
        except OSError as error:
            # 输出失败原因，但不终止其他文件的整理。
            log_callback(f"移动失败：{current_path.name}（{error}）")
            # 移动失败后，把失败数量加一。
            failed_count += 1

    # 如果没有任何可整理的文件，就给出明确提示。
    if found_count == 0:
        log_callback("没有找到需要整理的文件。")

    # AI 统计能给用户一个明确结论，避免“看起来没有执行”却不知道原因。
    if use_ai:
        if ai_eligible_count == 0:
            log_callback(
                "AI 重命名未执行：所选文件夹顶层没有 .txt 或 .md 文件。"
            )
        log_callback(
            f"AI 重命名统计：符合条件的文件 {ai_eligible_count} 个，"
            f"AI 调用尝试 {ai_attempted_count} 个，成功 {ai_success_count} 个，"
            f"失败 {ai_failure_count} 个。"
        )

    # 所有文件处理完成后，输出简短的结果摘要。
    log_callback(
        f"整理完成：成功移动了 {moved_count} 个文件，失败 {failed_count} 个文件。"
    )
    # 把统计结果返回给界面，用于更新按钮状态和弹窗提示。
    return moved_count, failed_count


# 定义撤销函数，根据移动记录把文件按相反顺序移回原位置。
def undo_moves(
    move_records: list[MoveRecord],
    log_callback=None,
) -> tuple[int, int, int]:
    """撤销一组文件移动，返回（恢复数量，跳过数量，失败数量）。

    如果目标文件已经被手动删除，就跳过并在日志中提示；
    如果原位置已经有同名文件，也会跳过，避免覆盖用户的新文件。
    """

    # 如果没有提供日志函数，就使用一个什么都不做的小函数。
    if log_callback is None:
        log_callback = lambda message: None

    # 初始化成功恢复的文件数量。
    restored_count = 0
    # 初始化因为文件不存在或路径冲突而跳过的数量。
    skipped_count = 0
    # 初始化恢复过程中发生错误的数量。
    failed_count = 0

    # 按相反顺序恢复文件，尽量还原连续移动前的状态。
    for record in reversed(move_records):
        # 读取记录中的原始路径和目标路径。
        original_path = record.original_path
        destination_path = record.destination_path

        # 单个文件失败时只记录错误，不影响其他文件继续撤销。
        try:
            # 如果整理后的文件已经被手动删除或被替换，就没有可恢复的内容。
            if not destination_path.is_file():
                log_callback(
                    f"撤销跳过：{destination_path.name}"
                    "（文件已不存在或已被替换，可能被手动删除）"
                )
                skipped_count += 1
                continue

            # 如果原位置已经有新的同名文件，跳过以避免覆盖。
            if original_path.exists():
                log_callback(
                    f"撤销跳过：{destination_path.name}"
                    "（原位置已有同名文件，为避免覆盖已跳过）"
                )
                skipped_count += 1
                continue

            # 如果原始文件夹被删除，先重新创建它。
            original_path.parent.mkdir(parents=True, exist_ok=True)
            # 把文件从整理后的位置移回原始路径。
            shutil.move(str(destination_path), str(original_path))
            # 输出恢复成功的日志。
            log_callback(
                f"已恢复：{destination_path.name} -> {original_path.parent}"
            )
            # 恢复成功后，把成功数量加一。
            restored_count += 1
        # 捕获文件被占用、权限不足等错误，继续处理下一条记录。
        except OSError as error:
            log_callback(f"撤销失败：{destination_path.name}（{error}）")
            failed_count += 1

    # 输出撤销总结，让界面日志区明确显示“撤销成功”。
    log_callback(
        f"撤销成功：已恢复 {restored_count} 个文件，"
        f"跳过 {skipped_count} 个，失败 {failed_count} 个。"
    )
    # 把统计结果返回给界面，用于显示撤销总结。
    return restored_count, skipped_count, failed_count


class DnDCTk(ctk.CTk, TkinterDnD.Tk):
    """同时保留 CustomTkinter 渲染能力和 TkDND 拖拽能力的根窗口。"""

    def __init__(self, *args, **kwargs) -> None:
        # 先让 CTk 完成窗口、缩放和外观初始化。CTk 会直接初始化底层 Tk，
        # 因此这里不能再次调用 TkinterDnD.Tk.__init__，否则会重复创建 Tcl
        # 解释器并破坏界面。DnDWrapper 的全局控件接口已经由导入过程注册。
        ctk.CTk.__init__(self, *args, **kwargs)
        # 再向现有 Tcl 解释器加载 tkdnd，这是同时使用 CTk 与 TkinterDnD
        # 时最稳定的方式；窗口仍由 CTk 完整管理，不需要替换控件体系。
        self.TkdndVersion = TkinterDnD.require(self)


class DiversionRuleRow:
    """可添加、删除和单独选择目标目录的一条分流规则。"""

    def __init__(
        self,
        app: OrganizerApp,
        parent: ctk.CTkScrollableFrame,
        category: str,
        target_directory: str,
    ) -> None:
        self.app = app
        self.type_var = tk.StringVar(
            value=DIVERSION_CATEGORY_LABELS[category]
        )
        self.path_var = tk.StringVar(value=target_directory)

        self.frame = ctk.CTkFrame(
            parent,
            fg_color=app.SOFT_SURFACE_COLOR,
            corner_radius=12,
            border_width=0,
        )
        self.frame.pack(fill="x", padx=4, pady=(0, 8))
        self.frame.grid_columnconfigure(1, weight=1)

        self.type_combo = ctk.CTkComboBox(
            self.frame,
            values=[label for label, _ in DIVERSION_OPTIONS],
            variable=self.type_var,
            state="readonly",
            width=112,
            height=38,
            corner_radius=9,
            border_width=0,
            fg_color=app.SURFACE_COLOR,
            button_color=app.SURFACE_COLOR,
            button_hover_color="#E2E8F0",
            dropdown_fg_color=app.SURFACE_COLOR,
            dropdown_hover_color="#EEF2FF",
            dropdown_text_color=app.TEXT_COLOR,
            text_color=app.TEXT_COLOR,
            text_color_disabled="#94A3B8",
            font=ctk.CTkFont(family=app.FONT_FAMILY, size=12),
            dropdown_font=ctk.CTkFont(family=app.FONT_FAMILY, size=12),
        )
        self.type_combo.grid(
            row=0,
            column=0,
            sticky="w",
            padx=(8, 0),
            pady=8,
        )

        self.path_entry = ctk.CTkEntry(
            self.frame,
            textvariable=self.path_var,
            placeholder_text="选择目标文件夹",
            state="readonly",
            height=38,
            corner_radius=9,
            border_width=0,
            fg_color=app.SURFACE_COLOR,
            text_color=app.TEXT_COLOR,
            placeholder_text_color="#94A3B8",
            font=ctk.CTkFont(family=app.FONT_FAMILY, size=11),
        )
        self.path_entry.grid(
            row=0,
            column=1,
            sticky="ew",
            padx=(8, 0),
            pady=8,
        )

        self.browse_button = ctk.CTkButton(
            self.frame,
            text="浏览",
            command=lambda: app._choose_diversion_folder(self),
            width=66,
            height=38,
            corner_radius=9,
            border_width=0,
            fg_color=app.SURFACE_COLOR,
            hover_color="#E9EEF8",
            text_color=app.TEXT_COLOR,
            font=ctk.CTkFont(
                family=app.FONT_FAMILY,
                size=11,
                weight="bold",
            ),
            cursor="hand2",
        )
        self.browse_button.grid(
            row=0,
            column=2,
            sticky="e",
            padx=(8, 0),
            pady=8,
        )

        self.delete_button = ctk.CTkButton(
            self.frame,
            text="×",
            command=self.delete,
            width=34,
            height=38,
            corner_radius=9,
            border_width=0,
            fg_color="transparent",
            hover_color="#FEE2E2",
            text_color="#DC2626",
            font=ctk.CTkFont(
                family=app.FONT_FAMILY,
                size=20,
                weight="bold",
            ),
            cursor="hand2",
        )
        self.delete_button.grid(
            row=0,
            column=3,
            sticky="e",
            padx=(4, 8),
            pady=8,
        )

    def delete(self) -> None:
        """从界面及规则集合中移除当前行。"""

        if self.app.is_running:
            return
        if self in self.app.diversion_rows:
            self.app.diversion_rows.remove(self)
        self.frame.destroy()
        self.app._refresh_diversion_empty_state()
        self.app._append_log("已删除一条自定义分流规则。")

    def selected_category(self) -> str | None:
        """把当前显示的类型文字转换成内部分类名称。"""

        selected_label = self.type_var.get()
        for label, category in DIVERSION_OPTIONS:
            if label == selected_label:
                return category
        return None

    def set_busy(self, busy: bool) -> None:
        """跟随整个任务切换当前行控件的可用状态。"""

        self.type_combo.configure(state="disabled" if busy else "readonly")
        self.path_entry.configure(state="disabled" if busy else "readonly")
        self.browse_button.configure(state="disabled" if busy else "normal")
        self.delete_button.configure(state="disabled" if busy else "normal")


class OrganizerApp:
    """文件整理助手的图形界面。"""

    # 使用接近现代 AI 产品网页的浅灰蓝底色。
    BACKGROUND_COLOR = "#F5F7FA"
    SURFACE_COLOR = "#FFFFFF"
    SOFT_SURFACE_COLOR = "#F1F5F9"
    TEXT_COLOR = "#0F172A"
    MUTED_COLOR = "#64748B"
    ACCENT_COLOR = "#4F46E5"
    ACCENT_HOVER_COLOR = "#6366F1"
    ACCENT_ACTIVE_COLOR = "#4338CA"
    LOG_BACKGROUND = "#111827"
    LOG_TEXT_COLOR = "#E5E7EB"
    LOG_MUTED_COLOR = "#94A3B8"
    SUCCESS_COLOR = "#22C55E"
    FONT_FAMILY = "Microsoft YaHei UI"

    def __init__(self, root: DnDCTk) -> None:
        # 保存主窗口对象。
        self.root = root
        # 配置固定放在程序目录，避免依赖启动时的工作目录。
        self.config_file = CONFIG_FILE
        config, self._config_load_warning = read_app_config(self.config_file)

        configured_work_directory = config.get("working_directory", "")
        if not isinstance(configured_work_directory, str):
            configured_work_directory = ""
        # 保存文件夹路径，输入框会实时显示它的内容。
        self.folder_path = tk.StringVar(value=configured_work_directory)

        configured_mode = config.get(
            "organize_mode",
            config.get("mode", MODE_BY_TYPE),
        )
        selected_mode_label = next(
            (
                label
                for label, mode in MODE_OPTIONS
                if mode == configured_mode
            ),
            MODE_OPTIONS[0][0],
        )
        # 保存下拉菜单的当前选项。
        self.selected_mode = tk.StringVar(value=selected_mode_label)
        # 保存 AI 复选框状态；默认关闭，避免普通整理也发起网络请求。
        self.use_ai = tk.BooleanVar(value=config.get("use_ai") is True)
        # 保存自定义分流的总开关；每条规则由动态行单独保存。
        self.use_diversion = tk.BooleanVar(
            value=config.get("diversion_enabled") is True
        )
        self.diversion_rows: list[DiversionRuleRow] = []
        self._restored_diversion_rules = normalize_diversion_rules(
            config.get("diversion_rules")
        )
        # 保存 API Key 输入框内容；已配置时只显示掩码，不回填真实 Key。
        self.api_key = tk.StringVar()
        # 记录界面上次读取或保存后，.env 中是否已经有 Key。
        self.api_key_configured = False
        # 保存 API Key 设置窗口，避免重复打开多个弹窗。
        self.api_dialog: ctk.CTkToplevel | None = None
        # 创建一个线程安全队列，用来接收后台线程发来的日志。
        self.message_queue: queue.Queue = queue.Queue()
        # 记录当前是否正在整理或撤销，防止重复点击。
        self.is_running = False
        # 保存最近一次整理产生的移动记录，供“撤销上一步”使用。
        self.undo_records: list[MoveRecord] = []

        # 设置窗口标题。
        self.root.title("文件整理助手")
        # 使用更舒展的初始尺寸，让大量留白和深色日志区同时保持呼吸感。
        self.root.geometry("980x940")
        # 保证较窄窗口下仍能完整显示主操作与日志区。
        self.root.minsize(780, 820)
        # 设置窗口背景颜色。
        self.root.configure(fg_color=self.BACKGROUND_COLOR)

        # 创建并摆放界面上的所有控件。
        self._build_interface()
        # 控件创建后再恢复规则行，确保下拉菜单和日志区已经可用。
        for rule in self._restored_diversion_rules:
            self._add_diversion_rule(
                rule.category,
                str(rule.target_directory),
                log=False,
            )
        self._refresh_diversion_empty_state()
        if self._config_load_warning:
            self._append_log(self._config_load_warning)
        # 无论通过标题栏按钮还是系统关闭事件退出，都保存当前界面状态。
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    def _build_interface(self) -> None:
        """使用 customtkinter 创建极简浅色界面。"""

        # 主容器只负责留白和纵向节奏，不再使用任何可见外框。
        main_frame = ctk.CTkFrame(
            self.root,
            fg_color="transparent",
            corner_radius=0,
        )
        main_frame.pack(fill="both", expand=True, padx=48, pady=34)
        main_frame.grid_columnconfigure(0, weight=1)
        # 只有日志区吸收多余高度，其他区域保持自然高度。
        main_frame.grid_rowconfigure(7, weight=1)

        # 顶部品牌与说明使用单列排版，保留充足呼吸空间。
        header_frame = ctk.CTkFrame(
            main_frame,
            fg_color="transparent",
            corner_radius=0,
        )
        header_frame.grid(row=0, column=0, sticky="ew", pady=(0, 28))

        title_label = ctk.CTkLabel(
            header_frame,
            text="文件整理助手",
            font=ctk.CTkFont(
                family=self.FONT_FAMILY,
                size=30,
                weight="bold",
            ),
            text_color=self.TEXT_COLOR,
            anchor="w",
        )
        title_label.pack(anchor="w")

        subtitle_label = ctk.CTkLabel(
            header_frame,
            text="选择目录，剩下的交给秩序。",
            font=ctk.CTkFont(family=self.FONT_FAMILY, size=13),
            text_color=self.MUTED_COLOR,
            anchor="w",
        )
        subtitle_label.pack(anchor="w", pady=(8, 0))

        # 路径区标题使用轻盈的小号标签。
        path_label = ctk.CTkLabel(
            main_frame,
            text="工作目录",
            font=ctk.CTkFont(
                family=self.FONT_FAMILY,
                size=12,
                weight="bold",
            ),
            text_color=self.TEXT_COLOR,
            anchor="w",
        )
        path_label.grid(row=1, column=0, sticky="w", pady=(0, 10))

        # 大圆角白色输入框模拟网页搜索框，前后不添加边框线。
        path_frame = ctk.CTkFrame(
            main_frame,
            fg_color="transparent",
            corner_radius=0,
        )
        path_frame.grid(row=2, column=0, sticky="ew")
        path_frame.grid_columnconfigure(0, weight=1)

        self.path_entry = ctk.CTkEntry(
            path_frame,
            textvariable=self.folder_path,
            placeholder_text="选择或粘贴要整理的文件夹路径",
            height=54,
            corner_radius=16,
            border_width=0,
            fg_color=self.SURFACE_COLOR,
            text_color=self.TEXT_COLOR,
            placeholder_text_color="#94A3B8",
            font=ctk.CTkFont(family=self.FONT_FAMILY, size=13),
        )
        self.path_entry.grid(row=0, column=0, sticky="ew")

        # 浏览按钮保持白色次级按钮样式，悬浮时只改变柔和底色。
        self.browse_button = ctk.CTkButton(
            path_frame,
            text="浏览",
            command=self._choose_folder,
            width=104,
            height=54,
            corner_radius=16,
            border_width=0,
            fg_color=self.SURFACE_COLOR,
            hover_color="#E9EEF8",
            text_color=self.TEXT_COLOR,
            font=ctk.CTkFont(
                family=self.FONT_FAMILY,
                size=13,
                weight="bold",
            ),
            cursor="hand2",
        )
        self.browse_button.grid(row=0, column=1, padx=(12, 0))

        # 将整理模式和 AI 开关放入一张无边框白色圆角卡片。
        mode_frame = ctk.CTkFrame(
            main_frame,
            fg_color=self.SURFACE_COLOR,
            corner_radius=20,
            border_width=0,
        )
        mode_frame.grid(row=3, column=0, sticky="ew", pady=(20, 0))
        mode_frame.grid_columnconfigure(0, weight=1)
        mode_frame.grid_columnconfigure(1, weight=1)

        mode_label = ctk.CTkLabel(
            mode_frame,
            text="整理偏好",
            font=ctk.CTkFont(
                family=self.FONT_FAMILY,
                size=12,
                weight="bold",
            ),
            text_color=self.TEXT_COLOR,
            anchor="w",
        )
        mode_label.grid(
            row=0,
            column=0,
            sticky="w",
            padx=(24, 0),
            pady=(20, 14),
        )

        # 分段按钮比传统下拉框更接近现代 AI 产品中的轻量选项切换。
        self.mode_combo = ctk.CTkSegmentedButton(
            mode_frame,
            values=[label for label, _ in MODE_OPTIONS],
            variable=self.selected_mode,
            height=40,
            corner_radius=12,
            border_width=0,
            fg_color=self.SOFT_SURFACE_COLOR,
            selected_color="#E0E7FF",
            selected_hover_color="#DBEAFE",
            unselected_color=self.SOFT_SURFACE_COLOR,
            unselected_hover_color="#E2E8F0",
            text_color=self.TEXT_COLOR,
            text_color_disabled="#94A3B8",
            font=ctk.CTkFont(family=self.FONT_FAMILY, size=12),
        )
        self.mode_combo.grid(
            row=1,
            column=0,
            sticky="w",
            padx=(24, 12),
            pady=(0, 20),
        )

        # AI 开关放在同一张卡片右侧，减少主界面的视觉层级。
        self.ai_checkbox = ctk.CTkSwitch(
            mode_frame,
            text="AI 智能重命名",
            variable=self.use_ai,
            onvalue=True,
            offvalue=False,
            width=0,
            fg_color="#CBD5E1",
            progress_color=self.ACCENT_COLOR,
            button_color="#FFFFFF",
            button_hover_color="#EEF2FF",
            text_color=self.TEXT_COLOR,
            font=ctk.CTkFont(
                family=self.FONT_FAMILY,
                size=12,
                weight="bold",
            ),
            cursor="hand2",
        )
        self.ai_checkbox.grid(
            row=0,
            column=1,
            sticky="e",
            padx=(12, 24),
            pady=(20, 14),
        )

        mode_hint = ctk.CTkLabel(
            mode_frame,
            text="仅处理 .txt / .md · 调用 DeepSeek",
            font=ctk.CTkFont(family=self.FONT_FAMILY, size=11),
            text_color=self.MUTED_COLOR,
            anchor="e",
        )
        mode_hint.grid(
            row=1,
            column=1,
            sticky="e",
            padx=(12, 24),
            pady=(0, 20),
        )

        # 自定义分流使用可滚动的规则列表；每行独立选择类型和目标目录。
        diversion_frame = ctk.CTkFrame(
            main_frame,
            fg_color=self.SURFACE_COLOR,
            corner_radius=18,
            border_width=1,
            border_color="#E2E8F0",
        )
        diversion_frame.grid(row=4, column=0, sticky="ew", pady=(14, 0))
        diversion_frame.grid_columnconfigure(0, weight=1)

        diversion_title = ctk.CTkLabel(
            diversion_frame,
            text="自定义分流",
            font=ctk.CTkFont(
                family=self.FONT_FAMILY,
                size=12,
                weight="bold",
            ),
            text_color=self.TEXT_COLOR,
            anchor="w",
        )
        diversion_title.grid(
            row=0,
            column=0,
            sticky="w",
            padx=(20, 12),
            pady=(14, 10),
        )

        self.diversion_checkbox = ctk.CTkCheckBox(
            diversion_frame,
            text="启用",
            variable=self.use_diversion,
            onvalue=True,
            offvalue=False,
            checkbox_width=20,
            checkbox_height=20,
            corner_radius=6,
            border_width=2,
            fg_color=self.ACCENT_COLOR,
            hover_color=self.ACCENT_HOVER_COLOR,
            border_color="#CBD5E1",
            checkmark_color="#FFFFFF",
            text_color=self.TEXT_COLOR,
            font=ctk.CTkFont(
                family=self.FONT_FAMILY,
                size=11,
                weight="bold",
            ),
            cursor="hand2",
        )
        self.diversion_checkbox.grid(
            row=0,
            column=1,
            sticky="e",
            padx=(8, 12),
            pady=(14, 10),
        )

        self.add_diversion_button = ctk.CTkButton(
            diversion_frame,
            text="+  添加分流规则",
            command=self._add_diversion_rule,
            width=132,
            height=34,
            corner_radius=9,
            border_width=0,
            fg_color="#EEF2FF",
            hover_color="#E0E7FF",
            text_color=self.ACCENT_COLOR,
            font=ctk.CTkFont(
                family=self.FONT_FAMILY,
                size=11,
                weight="bold",
            ),
            cursor="hand2",
        )
        self.add_diversion_button.grid(
            row=0,
            column=2,
            sticky="e",
            padx=(0, 20),
            pady=(14, 10),
        )

        self.diversion_rules_frame = ctk.CTkScrollableFrame(
            diversion_frame,
            height=158,
            corner_radius=12,
            border_width=0,
            fg_color="transparent",
            scrollbar_button_color="#CBD5E1",
            scrollbar_button_hover_color="#94A3B8",
            orientation="vertical",
        )
        self.diversion_rules_frame.grid(
            row=1,
            column=0,
            columnspan=3,
            sticky="ew",
            padx=16,
            pady=0,
        )

        self.diversion_empty_label = ctk.CTkLabel(
            self.diversion_rules_frame,
            text="还没有分流规则。添加后，匹配的文件会移动到各自目标目录。",
            font=ctk.CTkFont(family=self.FONT_FAMILY, size=11),
            text_color=self.MUTED_COLOR,
            anchor="center",
        )

        diversion_hint = ctk.CTkLabel(
            diversion_frame,
            text="按列表从上到下匹配；同一类型以第一条规则为准。",
            font=ctk.CTkFont(family=self.FONT_FAMILY, size=10),
            text_color=self.MUTED_COLOR,
            anchor="w",
        )
        diversion_hint.grid(
            row=2,
            column=0,
            columnspan=3,
            sticky="w",
            padx=20,
            pady=(8, 12),
        )

        # 主操作按钮使用清晰的单一靛蓝 CTA，并在悬浮时提亮。
        self.start_button = ctk.CTkButton(
            main_frame,
            text="开始整理",
            command=self._start_organizing,
            height=56,
            corner_radius=16,
            border_width=0,
            fg_color=self.ACCENT_COLOR,
            hover_color=self.ACCENT_HOVER_COLOR,
            text_color="#FFFFFF",
            text_color_disabled="#C7D2FE",
            font=ctk.CTkFont(
                family=self.FONT_FAMILY,
                size=15,
                weight="bold",
            ),
            cursor="hand2",
        )
        self.start_button.grid(
            row=5,
            column=0,
            sticky="ew",
            pady=(16, 24),
        )

        # 日志标题和绿色状态灯放在同一行，形成明确的信息层级。
        log_header = ctk.CTkFrame(
            main_frame,
            fg_color="transparent",
            corner_radius=0,
        )
        log_header.grid(row=6, column=0, sticky="ew", pady=(0, 10))
        log_header.grid_columnconfigure(0, weight=1)

        log_title = ctk.CTkLabel(
            log_header,
            text="整理日志",
            font=ctk.CTkFont(
                family=self.FONT_FAMILY,
                size=12,
                weight="bold",
            ),
            text_color=self.TEXT_COLOR,
            anchor="w",
        )
        log_title.grid(row=0, column=0, sticky="w")

        self.status_label = ctk.CTkLabel(
            log_header,
            text="●  就绪",
            font=ctk.CTkFont(
                family=self.FONT_FAMILY,
                size=11,
                weight="bold",
            ),
            text_color=self.SUCCESS_COLOR,
            anchor="e",
        )
        self.status_label.grid(row=0, column=1, sticky="e")

        # 深色终端风格日志区让浅色主界面拥有清晰的视觉锚点。
        self.log_text = ctk.CTkTextbox(
            main_frame,
            wrap="word",
            corner_radius=18,
            border_width=0,
            border_spacing=18,
            fg_color=self.LOG_BACKGROUND,
            text_color=self.LOG_TEXT_COLOR,
            scrollbar_button_color="#334155",
            scrollbar_button_hover_color="#475569",
            font=ctk.CTkFont(family="Cascadia Mono", size=12),
            activate_scrollbars=True,
            state="disabled",
        )
        self.log_text.grid(row=7, column=0, sticky="nsew")

        # 底部只保留两个低存在感操作，把 API Key 配置彻底收进弹窗。
        footer_frame = ctk.CTkFrame(
            main_frame,
            fg_color="transparent",
            corner_radius=0,
        )
        footer_frame.grid(row=8, column=0, sticky="ew", pady=(16, 0))
        footer_frame.grid_columnconfigure(0, weight=1)

        self.settings_button = ctk.CTkButton(
            footer_frame,
            text="⚙  设置 API Key",
            command=self._open_api_key_dialog,
            width=132,
            height=32,
            corner_radius=10,
            border_width=0,
            fg_color="transparent",
            hover_color="#E9EEF8",
            text_color=self.MUTED_COLOR,
            font=ctk.CTkFont(family=self.FONT_FAMILY, size=11),
            cursor="hand2",
        )
        self.settings_button.grid(row=0, column=0, sticky="w")

        self.undo_button = ctk.CTkButton(
            footer_frame,
            text="撤销上一步",
            command=self._start_undo,
            width=142,
            height=40,
            corner_radius=12,
            border_width=0,
            fg_color="transparent",
            hover_color="#E9EEF8",
            text_color="#475569",
            text_color_disabled="#94A3B8",
            font=ctk.CTkFont(
                family=self.FONT_FAMILY,
                size=12,
                weight="bold",
            ),
            cursor="arrow",
            state="disabled",
        )
        self.undo_button.grid(row=0, column=1, sticky="e")

        # 在程序启动时显示欢迎提示。
        self._append_log("请选择文件夹和整理方式，然后点击“开始整理”。")
        # 提醒用户撤销功能的作用范围。
        self._append_log("整理完成后，可点击“撤销上一步”恢复最近一次移动。")
        # 启动后把焦点放在路径输入框，方便直接输入。
        self.path_entry.focus_set()
        # 允许用户按回车键开始整理。
        self.path_entry.bind("<Return>", lambda event: self._start_organizing())
        # 初始化时没有撤销记录，让撤销按钮保持禁用样式。
        self._update_undo_button()
        # 初始化 Key 配置状态；真实 Key 不会进入主界面。
        self._refresh_api_key_status()
        # 等全部控件创建完成后统一注册拖拽目标，确保窗口任意位置都能接收文件夹。
        self._enable_folder_drop()

    def _enable_folder_drop(self) -> None:
        """把主窗口及全部子控件注册为文件夹放置目标。"""

        self._register_folder_drop_tree(self.root)

    def _register_folder_drop_tree(self, widget: tk.Misc) -> None:
        """递归注册控件，并在之后新增规则行时复用。"""

        # TkinterDnD 会为 BaseWidget 增加这些方法；逐个注册子控件可以
        # 覆盖输入框、日志区和空白背景，行为等同于拖到整个窗口。
        widget.drop_target_register(DND_FILES)
        widget.dnd_bind("<<Drop>>", self._handle_drop)
        for child in widget.winfo_children():
            self._register_folder_drop_tree(child)

    def _handle_drop(self, event: object) -> str:
        """解析拖入路径：文件夹写入输入框，单个文件只显示提示。"""

        # 后台任务进行中时保持当前路径不变，避免界面与正在执行的批次不一致。
        if self.is_running:
            return COPY

        raw_data = getattr(event, "data", "")
        try:
            # event.data 是 Tcl 列表；splitlist 能正确处理带空格和花括号的路径。
            dropped_items = self.root.tk.splitlist(raw_data)
        except (tk.TclError, ValueError):
            dropped_items = (raw_data,)

        dropped_paths = [
            Path(item).expanduser() for item in dropped_items if item
        ]
        folder = next(
            (path for path in dropped_paths if path.is_dir()),
            None,
        )
        if folder is None:
            self._append_log("请拖入一个文件夹，而不是单个文件")
            return COPY

        # 使用绝对路径填入输入框，和“浏览”按钮选择的路径保持相同格式。
        try:
            folder = folder.resolve()
        except OSError:
            folder = folder.absolute()
        self.folder_path.set(str(folder))
        self._append_log(f"已识别拖入的文件夹: {folder}")
        self.path_entry.focus_set()
        # TkDND 的放置回调需要返回接受的拖拽动作。
        return COPY

    def _refresh_diversion_empty_state(self) -> None:
        """根据规则数量和任务状态更新空列表提示与添加按钮。"""

        if self.diversion_rows:
            self.diversion_empty_label.pack_forget()
        else:
            self.diversion_empty_label.pack(
                fill="x",
                padx=18,
                pady=48,
            )

        add_button_enabled = (
            not self.is_running
            and len(self.diversion_rows) < len(DIVERSION_OPTIONS)
        )
        self.add_diversion_button.configure(
            state="normal" if add_button_enabled else "disabled",
            cursor="hand2" if add_button_enabled else "arrow",
        )

    def _add_diversion_rule(
        self,
        category: str | None = None,
        target_directory: str = "",
        log: bool = True,
    ) -> None:
        """新增一条规则，默认优先选择尚未使用的文件类型。"""

        if self.is_running:
            return
        if len(self.diversion_rows) >= len(DIVERSION_OPTIONS):
            if log:
                self._append_log("最多可添加四条分流规则，每种类型一条。")
            return

        valid_categories = {item for _, item in DIVERSION_OPTIONS}
        if category not in valid_categories:
            used_categories = {
                row.selected_category() for row in self.diversion_rows
            }
            category = next(
                (
                    option_category
                    for _, option_category in DIVERSION_OPTIONS
                    if option_category not in used_categories
                ),
                DIVERSION_OPTIONS[0][1],
            )

        row = DiversionRuleRow(
            self,
            self.diversion_rules_frame,
            category,
            target_directory,
        )
        self.diversion_rows.append(row)
        self._register_folder_drop_tree(row.frame)
        self._refresh_diversion_empty_state()
        if log:
            label = DIVERSION_CATEGORY_LABELS[category]
            self._append_log(f"已添加{label}分流规则，请选择目标文件夹。")

    def _diversion_rules_from_ui(self) -> list[DiversionRule]:
        """把当前规则行复制为后台线程可安全使用的不可变快照。"""

        rules: list[DiversionRule] = []
        for row in self.diversion_rows:
            category = row.selected_category()
            target_text = row.path_var.get().strip().strip('"')
            if category is None or not target_text:
                continue
            rules.append(
                DiversionRule(
                    category=category,
                    target_directory=Path(target_text).expanduser(),
                )
            )
        return rules

    def _current_config(self) -> dict:
        """生成与界面一一对应的 JSON 可序列化配置。"""

        diversion_rules = [
            {
                "category": rule.category,
                "target_directory": str(rule.target_directory),
            }
            for rule in self._diversion_rules_from_ui()
        ]
        return {
            "version": CONFIG_VERSION,
            "working_directory": self.folder_path.get().strip(),
            "use_ai": bool(self.use_ai.get()),
            "organize_mode": self._selected_mode(),
            "diversion_enabled": bool(self.use_diversion.get()),
            "diversion_rules": diversion_rules,
        }

    def _save_config(self) -> str:
        """保存当前配置并返回错误文本；成功时返回空字符串。"""

        try:
            write_app_config(self._current_config(), self.config_file)
        except (OSError, TypeError, ValueError) as error:
            return describe_exception(error)
        return ""

    def _on_close(self) -> None:
        """在窗口关闭前同步保存配置，然后退出事件循环。"""

        save_error = self._save_config()
        if save_error:
            messagebox.showwarning(
                "配置未保存",
                f"无法保存当前设置：\n{save_error}",
                parent=self.root,
            )
        self.root.destroy()

    def _refresh_api_key_status(self) -> None:
        """根据当前 .env 更新设置弹窗中的 Key 状态。"""

        self.api_key_configured = env_has_api_key()
        if self.api_key_configured:
            # 只显示固定长度的掩码，不把真实 Key 放进 Tk 变量。
            self.api_key.set(API_KEY_MASK)
        elif self.api_key.get() == API_KEY_MASK:
            self.api_key.set("")

        # 主界面保持纯净，状态提示只在 Key 设置弹窗打开时显示。
        if (
            self.api_dialog is not None
            and self.api_dialog.winfo_exists()
            and hasattr(self, "api_key_status_label")
        ):
            self.api_key_status_label.configure(
                text=(
                    "●  API Key 已配置"
                    if self.api_key_configured
                    else "○  API Key 未配置"
                ),
                text_color=(
                    self.SUCCESS_COLOR
                    if self.api_key_configured
                    else self.MUTED_COLOR
                ),
            )

    def _open_api_key_dialog(self) -> None:
        """打开隐藏的 API Key 设置窗口。"""

        if self.is_running:
            return

        if self.api_dialog is not None and self.api_dialog.winfo_exists():
            self._refresh_api_key_status()
            self.api_dialog.deiconify()
            self.api_dialog.lift()
            self.api_dialog.focus_force()
            return

        dialog = ctk.CTkToplevel(self.root)
        self.api_dialog = dialog
        dialog.title("API Key 设置")
        dialog.geometry("520x370")
        dialog.resizable(False, False)
        dialog.configure(fg_color=self.BACKGROUND_COLOR)
        dialog.transient(self.root)
        dialog.protocol("WM_DELETE_WINDOW", self._close_api_key_dialog)

        content = ctk.CTkFrame(
            dialog,
            fg_color="transparent",
            corner_radius=0,
        )
        content.pack(fill="both", expand=True, padx=34, pady=28)
        content.grid_columnconfigure(0, weight=1)

        title_label = ctk.CTkLabel(
            content,
            text="API Key 设置",
            font=ctk.CTkFont(
                family=self.FONT_FAMILY,
                size=22,
                weight="bold",
            ),
            text_color=self.TEXT_COLOR,
            anchor="w",
        )
        title_label.grid(row=0, column=0, sticky="w")

        description_label = ctk.CTkLabel(
            content,
            text="用于 DeepSeek 智能重命名，密钥只保存在本机 .env 文件中。",
            font=ctk.CTkFont(family=self.FONT_FAMILY, size=11),
            text_color=self.MUTED_COLOR,
            anchor="w",
            justify="left",
        )
        description_label.grid(row=1, column=0, sticky="w", pady=(8, 24))

        self.api_key_entry = ctk.CTkEntry(
            content,
            textvariable=self.api_key,
            show="*",
            height=52,
            corner_radius=15,
            border_width=0,
            fg_color=self.SURFACE_COLOR,
            text_color=self.TEXT_COLOR,
            placeholder_text="输入新的 API Key",
            placeholder_text_color="#94A3B8",
            font=ctk.CTkFont(family=self.FONT_FAMILY, size=13),
        )
        self.api_key_entry.grid(row=2, column=0, sticky="ew")

        self.api_key_status_label = ctk.CTkLabel(
            content,
            text="",
            font=ctk.CTkFont(
                family=self.FONT_FAMILY,
                size=11,
                weight="bold",
            ),
            text_color=self.MUTED_COLOR,
            anchor="w",
        )
        self.api_key_status_label.grid(
            row=3,
            column=0,
            sticky="w",
            pady=(10, 0),
        )

        button_frame = ctk.CTkFrame(
            content,
            fg_color="transparent",
            corner_radius=0,
        )
        button_frame.grid(row=4, column=0, sticky="ew", pady=(24, 0))
        button_frame.grid_columnconfigure(0, weight=1)

        # 保存 Key 保持次级视觉，真正的主 CTA 仍然只有“开始整理”。
        self.save_api_key_button = ctk.CTkButton(
            button_frame,
            text="保存 Key",
            command=self._save_api_key,
            width=116,
            height=42,
            corner_radius=12,
            border_width=0,
            fg_color=self.SURFACE_COLOR,
            hover_color="#E9EEF8",
            text_color=self.TEXT_COLOR,
            font=ctk.CTkFont(
                family=self.FONT_FAMILY,
                size=12,
                weight="bold",
            ),
            cursor="hand2",
        )
        self.save_api_key_button.grid(row=0, column=1, padx=(10, 0))

        close_button = ctk.CTkButton(
            button_frame,
            text="取消",
            command=self._close_api_key_dialog,
            width=82,
            height=42,
            corner_radius=12,
            border_width=0,
            fg_color="transparent",
            hover_color="#E9EEF8",
            text_color=self.MUTED_COLOR,
            font=ctk.CTkFont(family=self.FONT_FAMILY, size=12),
            cursor="hand2",
        )
        close_button.grid(row=0, column=2, padx=(8, 0))

        # 已配置时聚焦输入框会自动选中掩码，直接输入即可覆盖。
        self.api_key_entry.bind("<FocusIn>", self._prepare_api_key_edit)
        self.api_key_entry.bind(
            "<Return>",
            lambda _event: self._save_api_key(),
        )
        self._refresh_api_key_status()
        dialog.after(20, dialog.grab_set)
        dialog.after(40, self.api_key_entry.focus_set)

    def _close_api_key_dialog(self) -> None:
        """关闭 API Key 设置弹窗并清理输入内容。"""

        dialog = self.api_dialog
        self.api_dialog = None
        self.api_key.set("")
        if dialog is None or not dialog.winfo_exists():
            return
        try:
            dialog.grab_release()
        except tk.TclError:
            pass
        dialog.destroy()

    def _prepare_api_key_edit(self, _event: tk.Event) -> None:
        """双击或聚焦已配置的掩码时，自动全选以便直接输入新 Key。"""

        if self.api_key.get() == API_KEY_MASK:
            # 等 Entry 的默认点击处理完成后再选中，避免光标点击清掉选区。
            self.root.after_idle(self._select_api_key_mask)

    def _select_api_key_mask(self) -> None:
        """选中 Key 掩码，用户键入的第一个字符会直接替换它。"""

        if self.api_key.get() == API_KEY_MASK:
            entry_widget = getattr(self.api_key_entry, "_entry", None)
            if entry_widget is not None:
                entry_widget.selection_range(0, "end")
                entry_widget.icursor("end")

    def _save_api_key(self) -> None:
        """把输入框中的新 Key 保存到 .env。"""

        if self.is_running:
            return

        dialog_parent = self.api_dialog or self.root
        api_key = self.api_key.get().strip()
        if api_key == API_KEY_MASK:
            self._refresh_api_key_status()
            if self.api_key_configured:
                messagebox.showinfo(
                    "保存成功",
                    "API Key 已配置，无需重复保存。",
                    parent=dialog_parent,
                )
                self._close_api_key_dialog()
            else:
                self.api_key.set("")
                messagebox.showwarning(
                    "API Key 为空",
                    "请先填写 API Key。",
                    parent=dialog_parent,
                )
                self.api_key_entry.focus_set()
            return

        if not api_key:
            messagebox.showwarning(
                "API Key 为空",
                "请先填写 API Key。",
                parent=dialog_parent,
            )
            self.api_key_entry.focus_set()
            return

        try:
            write_api_key_to_env(api_key)
        except (OSError, ValueError) as error:
            messagebox.showerror(
                "保存失败",
                f"无法保存 API Key：\n{error}",
                parent=dialog_parent,
            )
            return

        # 保存后立即隐藏真实 Key，并提示下次 AI 调用会自动重新加载配置。
        self._refresh_api_key_status()
        self._append_log("API Key 已保存，将在下一次 AI 调用前自动加载。")
        messagebox.showinfo(
            "保存成功",
            "API Key 已保存。",
            parent=dialog_parent,
        )
        self._close_api_key_dialog()

    def _choose_folder(self) -> None:
        """打开系统文件夹选择窗口。"""

        # 如果输入框中已经有一个有效路径，就从这个路径开始浏览。
        current_path = self.folder_path.get().strip().strip('"')
        # 判断当前路径是不是一个存在的文件夹。
        if current_path and Path(current_path).expanduser().is_dir():
            initial_directory = str(Path(current_path).expanduser())
        else:
            # 否则从用户的主目录开始浏览。
            initial_directory = str(Path.home())

        # 打开系统自带的“选择文件夹”窗口。
        selected_folder = filedialog.askdirectory(
            parent=self.root,
            title="选择要整理的文件夹",
            initialdir=initial_directory,
            mustexist=True,
        )
        # 如果用户点击了“取消”，返回值是空字符串，此时不修改输入框。
        if selected_folder:
            # 把选中的文件夹路径显示在输入框中。
            self.folder_path.set(selected_folder)
            # 记录用户已经选择了文件夹。
            self._append_log(f"已选择文件夹：{selected_folder}")

    def _choose_diversion_folder(self, row: DiversionRuleRow) -> None:
        """为指定规则行选择自定义分流目标。"""

        if self.is_running:
            return

        # 优先从已选分流目录开始浏览，其次定位到当前工作目录。
        current_diversion = row.path_var.get().strip().strip('"')
        current_source = self.folder_path.get().strip().strip('"')
        if current_diversion and Path(current_diversion).expanduser().is_dir():
            initial_directory = str(Path(current_diversion).expanduser())
        elif current_source and Path(current_source).expanduser().is_dir():
            initial_directory = str(Path(current_source).expanduser())
        else:
            initial_directory = str(Path.home())

        selected_folder = filedialog.askdirectory(
            parent=self.root,
            title="选择自定义分流目标文件夹",
            initialdir=initial_directory,
            mustexist=True,
        )
        if not selected_folder:
            return

        selected_path = Path(selected_folder).expanduser()
        try:
            selected_path = selected_path.resolve()
            source_resolved = (
                Path(current_source).expanduser().resolve()
                if current_source
                else None
            )
        except (OSError, RuntimeError):
            selected_path = selected_path.absolute()
            source_resolved = (
                Path(current_source).expanduser().absolute()
                if current_source
                else None
            )

        # 同目录无法形成分流；在选目录时直接阻止，比整理后再回退更清楚。
        if source_resolved is not None and selected_path == source_resolved:
            messagebox.showwarning(
                "目标文件夹无效",
                "分流目标不能与工作目录相同，请选择其他文件夹。",
                parent=self.root,
            )
            return

        row.path_var.set(str(selected_path))
        category_label = row.type_var.get()
        self._append_log(
            f"已选择{category_label}分流目标：{selected_path}"
        )

    def _selected_mode(self) -> str:
        """把下拉菜单显示的文字转换成内部整理模式。"""

        # 读取当前显示的文字。
        selected_label = self.selected_mode.get()
        # 在选项表中查找对应的内部模式名称。
        for label, mode in MODE_OPTIONS:
            if label == selected_label:
                return mode
        # 如果选项意外为空，就回退到原来的按类型整理。
        return MODE_BY_TYPE

    def _set_busy(self, busy: bool, action_text: str = "") -> None:
        """统一切换整理或撤销期间的控件状态。"""

        if busy:
            # 标记当前正在执行后台任务。
            self.is_running = True
            # 禁用开始按钮，并用文字显示当前正在做什么。
            self.start_button.configure(
                state="disabled",
                text=action_text,
                cursor="arrow",
            )
            # 任务执行期间禁用撤销按钮，避免同时修改文件。
            self.undo_button.configure(state="disabled", cursor="arrow")
            # 暂时禁用路径、浏览按钮和整理方式，避免用户误以为修改会立即生效。
            self.path_entry.configure(state="disabled")
            self.browse_button.configure(state="disabled")
            self.mode_combo.configure(state="disabled")
            self.settings_button.configure(state="disabled")
            # 任务开始后不再允许改变 AI 开关，保证整个批次行为一致。
            self.ai_checkbox.configure(state="disabled")
            # 分流开关和所有规则行同样固定，确保后台使用点击时的快照。
            self.diversion_checkbox.configure(state="disabled")
            self.add_diversion_button.configure(
                state="disabled",
                cursor="arrow",
            )
            for row in self.diversion_rows:
                row.set_busy(True)
            self.status_label.configure(text="●  正在处理")
            return

        # 后台任务结束后，恢复所有输入控件的可用状态。
        self.is_running = False
        self.start_button.configure(
            state="normal",
            text="开始整理",
            cursor="hand2",
        )
        self.path_entry.configure(state="normal")
        self.browse_button.configure(state="normal")
        self.mode_combo.configure(state="normal")
        self.settings_button.configure(state="normal")
        # 任务结束后重新允许用户选择是否使用 AI。
        self.ai_checkbox.configure(state="normal")
        # 目标路径始终恢复为只读，其他分流控件恢复可操作状态。
        self.diversion_checkbox.configure(state="normal")
        for row in self.diversion_rows:
            row.set_busy(False)
        self._refresh_diversion_empty_state()
        self.status_label.configure(text="●  就绪")
        # 根据是否还有撤销记录决定撤销按钮是否可用。
        self._update_undo_button()

    def _update_undo_button(self) -> None:
        """根据当前记录和运行状态更新撤销按钮。"""

        # 有记录且没有后台任务时，才允许撤销。
        if self.undo_records and not self.is_running:
            self.undo_button.configure(state="normal", cursor="hand2")
        else:
            self.undo_button.configure(state="disabled", cursor="arrow")

    def _start_organizing(self) -> None:
        """检查输入内容，并在后台开始整理文件。"""

        # 如果已经在整理或撤销，就忽略这次点击。
        if self.is_running:
            return

        # 点击即保存，即使路径校验随后失败，也保留下一次启动所需的界面状态。
        config_save_error = self._save_config()

        # 读取输入框中的路径，并去掉首尾空格和可能带上的双引号。
        raw_path = self.folder_path.get().strip().strip('"')
        # 如果没有填写路径，就提示用户先选择文件夹。
        if not raw_path:
            messagebox.showwarning("还没有选择文件夹", "请先选择要整理的文件夹。")
            # 把光标放回输入框。
            self.path_entry.focus_set()
            return

        # 把用户输入的文本转换成路径对象。
        source_directory = Path(raw_path).expanduser()
        # 检查路径是否存在。
        if not source_directory.exists():
            messagebox.showerror("文件夹不存在", f"找不到这个文件夹：\n{source_directory}")
            return
        # 检查路径是不是文件夹，而不是一个文件。
        if not source_directory.is_dir():
            messagebox.showerror("路径有误", f"这个路径不是文件夹：\n{source_directory}")
            return

        # 统一使用绝对路径，确保撤销记录在切换工作目录后仍然可靠。
        try:
            source_directory = source_directory.resolve()
        except OSError:
            source_directory = source_directory.absolute()

        # 在点击时固定本次使用的整理模式，避免后台运行期间选项变化。
        mode = self._selected_mode()
        # 同样固定 AI 开关状态，避免后台线程读到随后变化的界面变量。
        use_ai = self.use_ai.get()
        # 固定所有分流规则，整个批次只使用点击时的一组配置。
        diversion_enabled = self.use_diversion.get()
        diversion_rules = (
            self._diversion_rules_from_ui() if diversion_enabled else []
        )
        # 记录用户选择的模式名称，方便日志阅读。
        mode_label = self.selected_mode.get()

        # 清空上一次的日志，准备显示本次整理过程。
        self._clear_log()
        if config_save_error:
            self._append_log(f"配置保存失败：{config_save_error}")
        # 记录本次整理的文件夹和整理方式。
        self._append_log(f"开始整理：{source_directory}")
        self._append_log(f"整理方式：{mode_label}")
        self._append_log(
            "AI 智能重命名：已启用（仅处理 .txt 和 .md）"
            if use_ai
            else "AI 智能重命名：未启用"
        )
        if diversion_enabled:
            if diversion_rules:
                self._append_log(
                    f"自定义分流：已启用，共 {len(diversion_rules)} 条规则"
                )
            else:
                self._append_log("自定义分流：已启用，但没有完整规则")
        else:
            self._append_log("自定义分流：未启用")
        # 标记程序正在执行后台任务，并禁用相关控件。
        self._set_busy(True, "正在整理…")

        # 创建后台线程，把耗时的文件移动操作放到界面线程之外。
        worker = threading.Thread(
            target=self._organize_in_background,
            args=(
                source_directory,
                mode,
                use_ai,
                diversion_rules,
            ),
            daemon=True,
        )
        # 启动后台线程。
        worker.start()
        # 开始定期检查后台线程发来的日志。
        self.root.after(100, self._process_messages)

    def _organize_in_background(
        self,
        source_directory: Path,
        mode: str,
        use_ai: bool,
        diversion_rules: list[DiversionRule],
    ) -> None:
        """在后台线程中执行整理，并通过队列把消息传回界面。"""

        # 把日志函数包装成“把消息放进队列”的函数。
        def send_log(message: str) -> None:
            self.message_queue.put(("log", message))

        # 创建本次整理的移动记录列表，成功移动一个就追加一条。
        move_records: list[MoveRecord] = []

        # 使用异常处理，防止意外错误导致界面一直停在“正在整理”。
        try:
            # 调用整理函数，并跳过正在运行的程序文件。
            moved_count, failed_count = organize_directory(
                source_directory,
                # 同时保护图形界面程序和原来的 organizer.py，避免整理时把它们移走。
                skip_paths={
                    Path(__file__),
                    Path(__file__).resolve().with_name("organizer.py"),
                    # AI 模块和密钥配置也是程序运行依赖，整理项目目录时不能移走。
                    Path(__file__).resolve().with_name("ai_renamer.py"),
                    Path(__file__).resolve().with_name(".env"),
                    # 配置是程序下次启动依赖，整理源码目录时也必须保留原位。
                    self.config_file,
                },
                log_callback=send_log,
                mode=mode,
                move_records=move_records,
                use_ai=use_ai,
                diversion_rules=diversion_rules,
            )
            # 把整理结果和移动记录一起发送给界面线程。
            self.message_queue.put(
                ("done", moved_count, failed_count, move_records)
            )
        except Exception as error:
            # 即使发生意外错误，也把已经成功移动的记录带回界面，保证还能撤销。
            self.message_queue.put(("error", str(error), move_records))

    def _start_undo(self) -> None:
        """在后台线程中撤销最近一次整理。"""

        # 正在执行其他任务或没有记录时，不执行撤销。
        if self.is_running or not self.undo_records:
            return

        # 复制一份记录，避免后台线程读取时界面状态发生变化。
        records = list(self.undo_records)
        # 清空日志并提示即将撤销的文件数量。
        self._clear_log()
        self._append_log(f"开始撤销：共 {len(records)} 条移动记录。")
        # 禁用按钮，并把开始按钮的文字切换为撤销状态。
        self._set_busy(True, "正在撤销…")

        # 撤销也可能涉及大量文件，因此同样放到后台线程。
        worker = threading.Thread(
            target=self._undo_in_background,
            args=(records,),
            daemon=True,
        )
        worker.start()
        # 开始定期检查后台线程发来的日志和结果。
        self.root.after(100, self._process_messages)

    def _undo_in_background(self, records: list[MoveRecord]) -> None:
        """在后台线程中执行撤销，并通过队列把结果传回界面。"""

        # 把日志函数包装成“把消息放进队列”的函数。
        def send_log(message: str) -> None:
            self.message_queue.put(("log", message))

        try:
            # 调用撤销函数，单个文件缺失或失败不会中断整个撤销过程。
            restored_count, skipped_count, failed_count = undo_moves(
                records,
                log_callback=send_log,
            )
            # 把撤销统计结果发送给界面线程。
            self.message_queue.put(
                ("undo_done", restored_count, skipped_count, failed_count)
            )
        except Exception as error:
            # 把意外错误发送给界面线程显示。
            self.message_queue.put(("undo_error", str(error)))

    def _process_messages(self) -> None:
        """从队列中取出后台消息，并更新界面。"""

        # 循环取出队列中当前已经到达的所有消息。
        while True:
            try:
                message = self.message_queue.get_nowait()
            except queue.Empty:
                # 队列为空时结束本轮处理。
                break

            # 如果是日志消息，就追加到日志文本框。
            if message[0] == "log":
                self._append_log(message[1])
            # 如果是整理完成消息，就恢复按钮状态并显示结果。
            elif message[0] == "done":
                self._finish_organizing(message[1], message[2], message[3])
                return
            # 如果是撤销完成消息，就显示撤销结果。
            elif message[0] == "undo_done":
                self._finish_undo(message[1], message[2], message[3])
                return
            # 如果是整理过程中的意外错误，就显示错误并恢复按钮状态。
            elif message[0] == "error":
                self._handle_organize_error(message[1], message[2])
                return
            # 如果是撤销过程中的意外错误，就保留记录供用户重试。
            elif message[0] == "undo_error":
                self._handle_undo_error(message[1])
                return

        # 如果后台任务仍在进行，就继续等待下一次检查。
        if self.is_running:
            self.root.after(100, self._process_messages)

    def _finish_organizing(
        self,
        moved_count: int,
        failed_count: int,
        move_records: list[MoveRecord],
    ) -> None:
        """整理结束后恢复界面，并告诉用户最终结果。"""

        # 恢复所有控件的可用状态。
        self._set_busy(False)

        # 如果本次确实移动了文件，就用最新记录替换可撤销记录。
        if move_records:
            self.undo_records = list(move_records)
            self._append_log(
                f"已保存撤销记录：共 {len(self.undo_records)} 个文件。"
            )
        # 如果本次没有移动文件但之前有记录，就保留之前的撤销能力。
        elif self.undo_records:
            self._append_log("本次没有移动文件，仍可撤销上一次整理。")
        # 根据最新记录更新撤销按钮。
        self._update_undo_button()

        # 如果没有任何文件被移动或失败，说明文件夹中没有可整理的文件。
        if moved_count == 0 and failed_count == 0:
            messagebox.showinfo("整理完成", "没有找到需要整理的文件。")
        # 如果没有失败文件，就显示成功移动的数量。
        elif failed_count == 0:
            messagebox.showinfo(
                "整理完成",
                f"成功移动了 {moved_count} 个文件。\n\n"
                "如需恢复，可点击“撤销上一步”。",
            )
        # 如果有失败文件，就同时显示成功和失败的数量。
        else:
            messagebox.showwarning(
                "整理完成（部分文件失败）",
                f"成功移动了 {moved_count} 个文件，"
                f"有 {failed_count} 个文件移动失败。\n\n"
                "请查看日志中的详细原因。",
            )

    def _handle_organize_error(
        self,
        error_message: str,
        move_records: list[MoveRecord],
    ) -> None:
        """处理整理阶段的意外错误，并尽量保留撤销记录。"""

        # 把错误写入日志。
        self._append_log(f"程序发生错误：{error_message}")
        # 如果错误发生前已经移动了部分文件，仍然允许用户撤销。
        if move_records:
            self.undo_records = list(move_records)
            self._append_log(
                f"已记录 {len(self.undo_records)} 个已移动文件，"
                "仍可点击“撤销上一步”。"
            )
        # 恢复界面按钮状态。
        self._set_busy(False)
        messagebox.showerror("整理失败", f"整理过程中发生错误：\n{error_message}")

    def _finish_undo(
        self,
        restored_count: int,
        skipped_count: int,
        failed_count: int,
    ) -> None:
        """撤销结束后恢复界面，并显示撤销统计。"""

        # 恢复所有控件的可用状态。
        self._set_busy(False)
        # 撤销流程已经执行完毕，清空本次记录。
        self.undo_records = []
        self._update_undo_button()

        # 全部恢复成功时显示普通提示。
        if skipped_count == 0 and failed_count == 0:
            messagebox.showinfo(
                "撤销成功",
                f"已恢复 {restored_count} 个文件。",
            )
        # 有文件缺失、冲突或失败时，提醒用户查看日志。
        else:
            messagebox.showwarning(
                "撤销完成（部分文件未恢复）",
                f"已恢复 {restored_count} 个文件，"
                f"跳过 {skipped_count} 个，失败 {failed_count} 个。\n\n"
                "被手动删除或原位置已被占用的文件会跳过，"
                "请查看日志中的详细原因。",
            )

    def _handle_undo_error(self, error_message: str) -> None:
        """处理撤销阶段的意外错误，并保留记录供用户重试。"""

        # 把错误写入日志。
        self._append_log(f"撤销发生错误：{error_message}")
        # 恢复界面按钮状态；记录不会清空，用户可以再次尝试。
        self._set_busy(False)
        messagebox.showerror("撤销失败", f"撤销过程中发生错误：\n{error_message}")

    def _append_log(self, message: str) -> None:
        """向日志文本框末尾添加一行带时间的消息。"""

        # 生成当前时间，方便用户查看整理过程。
        timestamp = datetime.now().strftime("%H:%M:%S")
        # 临时允许编辑文本框。
        self.log_text.configure(state="normal")
        # 在末尾插入时间和消息。
        self.log_text.insert("end", f"[{timestamp}] {message}\n")
        # 自动滚动到最新一行。
        self.log_text.see("end")
        # 重新设为只读，防止用户误改日志。
        self.log_text.configure(state="disabled")

    def _clear_log(self) -> None:
        """清空日志文本框。"""

        # 临时允许编辑文本框。
        self.log_text.configure(state="normal")
        # 删除全部内容。
        self.log_text.delete("1.0", "end")
        # 重新设为只读。
        self.log_text.configure(state="disabled")


# 定义启动图形界面的函数。
def main() -> None:
    """创建 customtkinter 主窗口并进入事件循环。"""

    # 强制使用极简浅色模式，避免系统深色偏好改变最终视觉效果。
    ctk.set_appearance_mode("light")
    ctk.set_default_color_theme("blue")
    # 使用固定像素布局，保证 125% / 150% 系统缩放下也不裁切右侧控件。
    ctk.deactivate_automatic_dpi_awareness()

    # 创建同时支持 CustomTkinter 和文件夹拖拽的根窗口。
    root = DnDCTk(fg_color=OrganizerApp.BACKGROUND_COLOR)
    # 设置窗口左上角图标；找不到图标时继续正常启动程序。
    try:
        root.iconbitmap("app_icon.ico")
    except (tk.TclError, OSError):
        pass
    # 创建程序界面。
    OrganizerApp(root)
    # 启动事件循环，窗口会一直显示，直到用户关闭它。
    root.mainloop()


# 只有直接运行这个文件时才启动图形界面。
if __name__ == "__main__":
    main()
