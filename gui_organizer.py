# -*- coding: utf-8 -*-
"""文件整理助手（图形界面版）。

这个程序使用 Python 自带的 tkinter 库，为原有的 organizer.py 加上图形界面。
它保留下面的整理逻辑：
1. 只整理所选文件夹顶层的文件，不会递归进入子文件夹。
2. 支持按扩展名分类，以及按文件修改月份归档两种整理方式。
3. 如果目标文件夹中有同名文件，会自动生成“文件名 (1).扩展名”这样的新名称。
4. 每次整理都会记录文件的原始路径和目标路径，支持一键撤销最近一次整理。
"""

# 延迟解析类型注解，让较旧版本的 Python 也能正常读取这个文件。
from __future__ import annotations

# 导入队列模块，用于让后台整理线程把日志安全地传给界面线程。
import queue
# 导入 shutil 模块，用来移动文件。
import shutil
# 导入 threading 模块，让文件整理和撤销都在后台运行，避免窗口卡住。
import threading
# 导入 tkinter，它是 Python 自带的图形界面库。
import tkinter as tk
# 从 dataclasses 中导入 dataclass，用来保存每一次文件移动记录。
from dataclasses import dataclass
# 从 datetime 中导入 datetime，用来生成月份文件夹名称和日志时间。
from datetime import datetime
# 从 pathlib 中导入 Path，用来更方便地处理文件路径。
from pathlib import Path
# 从 tkinter 中导入文件选择窗口、消息框和主题控件。
from tkinter import filedialog, messagebox, ttk


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


# 定义两种整理模式的内部名称，避免界面文字和业务逻辑直接耦合。
MODE_BY_TYPE = "type"
MODE_BY_TIME = "time"

# 定义下拉菜单中显示的选项；每一项由“显示文字”和“内部模式名称”组成。
MODE_OPTIONS = (
    ("按文件类型分类", MODE_BY_TYPE),
    ("按修改时间分类", MODE_BY_TIME),
)


@dataclass(frozen=True)
class MoveRecord:
    """记录一次成功移动，撤销时按相反顺序把文件移回原位置。"""

    # 保存文件整理前的原始路径。
    original_path: Path
    # 保存文件整理后的目标路径。
    destination_path: Path


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


# 定义整理函数，接收要整理的文件夹路径、整理模式和日志回调函数。
def organize_directory(
    source_directory: Path,
    skip_paths: set[Path] | None = None,
    log_callback=None,
    mode: str = MODE_BY_TYPE,
    move_records: list[MoveRecord] | None = None,
) -> tuple[int, int]:
    """整理指定文件夹，并返回（成功数量，失败数量）。

    source_directory：要整理的文件夹。
    skip_paths：整理时需要跳过的文件路径，例如正在运行的程序本身。
    log_callback：接收日志文本的函数；传入 None 时不输出过程信息。
    mode：整理模式，MODE_BY_TYPE 表示按类型，MODE_BY_TIME 表示按修改月份。
    move_records：用于收集成功移动记录的列表，供“撤销上一步”使用。
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

    # 保存需要跳过的文件路径，统一转换成绝对路径，方便比较。
    protected_paths = {path.resolve() for path in (skip_paths or set())}
    # 初始化成功移动的文件数量。
    moved_count = 0
    # 初始化移动失败的文件数量。
    failed_count = 0
    # 初始化找到的可整理文件数量。
    found_count = 0

    # 遍历所选文件夹中的每一个项目，只处理最外层，不进入子文件夹。
    for file_path in source_directory.iterdir():
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
            # 根据用户选择的模式决定分类文件夹名称。
            if mode == MODE_BY_TIME:
                category = get_month_category(file_path)
            else:
                category = get_category(file_path)

            # 拼接分类文件夹的完整路径。
            target_directory = source_directory / category
            # 创建分类文件夹；如果文件夹已经存在，不会报错。
            target_directory.mkdir(parents=True, exist_ok=True)
            # 计算不会覆盖已有文件的目标路径。
            destination = build_destination(target_directory, file_path.name)
            # 把当前文件从原位置移动到分类文件夹中。
            shutil.move(str(file_path), str(destination))
            # 只有移动成功后，才保存撤销所需的原始路径和目标路径。
            move_records.append(
                MoveRecord(
                    original_path=file_path.absolute(),
                    destination_path=destination.absolute(),
                )
            )
            # 把本次移动结果写入日志。
            log_callback(f"已移动：{file_path.name} -> {category}/")
            # 成功移动后，把成功数量加一。
            moved_count += 1
        # 捕获文件移动过程中可能出现的系统或权限错误。
        except OSError as error:
            # 输出失败原因，但不终止其他文件的整理。
            log_callback(f"移动失败：{file_path.name}（{error}）")
            # 移动失败后，把失败数量加一。
            failed_count += 1

    # 如果没有任何可整理的文件，就给出明确提示。
    if found_count == 0:
        log_callback("没有找到需要整理的文件。")
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


class OrganizerApp:
    """文件整理助手的图形界面。"""

    # 设置窗口背景颜色。
    BACKGROUND_COLOR = "#f5f7fb"
    # 设置主要文字颜色。
    TEXT_COLOR = "#1f2937"
    # 设置次要文字颜色。
    MUTED_COLOR = "#6b7280"
    # 设置“开始整理”按钮的颜色。
    ACCENT_COLOR = "#2563eb"
    # 设置“开始整理”按钮被按下时的颜色。
    ACCENT_ACTIVE_COLOR = "#1d4ed8"
    # 设置次要按钮的按下背景颜色。
    SECONDARY_ACTIVE_COLOR = "#eff6ff"

    def __init__(self, root: tk.Tk) -> None:
        # 保存主窗口对象。
        self.root = root
        # 保存文件夹路径，输入框会实时显示它的内容。
        self.folder_path = tk.StringVar()
        # 保存下拉菜单的当前选项。
        self.selected_mode = tk.StringVar(value=MODE_OPTIONS[0][0])
        # 创建一个线程安全队列，用来接收后台线程发来的日志。
        self.message_queue: queue.Queue = queue.Queue()
        # 记录当前是否正在整理或撤销，防止重复点击。
        self.is_running = False
        # 保存最近一次整理产生的移动记录，供“撤销上一步”使用。
        self.undo_records: list[MoveRecord] = []

        # 设置窗口标题。
        self.root.title("文件整理助手")
        # 设置窗口初始大小，为新增的下拉菜单和撤销按钮留出空间。
        self.root.geometry("820x660")
        # 设置窗口最小尺寸，避免控件被挤得过小。
        self.root.minsize(680, 560)
        # 设置窗口背景颜色。
        self.root.configure(bg=self.BACKGROUND_COLOR)

        # 创建并摆放界面上的所有控件。
        self._build_interface()

    def _build_interface(self) -> None:
        """创建窗口中的所有控件。"""

        # 为下拉菜单单独设置字体，避免不同系统主题下文字过小。
        ttk.Style(self.root).configure(
            "Mode.TCombobox",
            font=("Microsoft YaHei UI", 10),
        )

        # 创建主容器，四周留出空白，让界面看起来更整洁。
        main_frame = tk.Frame(self.root, bg=self.BACKGROUND_COLOR, padx=28, pady=22)
        # 让主容器填满窗口，并在窗口放大时自动伸缩。
        main_frame.pack(fill="both", expand=True)
        # 设置第一行（标题）按内容高度显示。
        main_frame.rowconfigure(0, weight=0)
        # 设置第二行（说明文字）按内容高度显示。
        main_frame.rowconfigure(1, weight=0)
        # 设置第三行（路径选择）按内容高度显示。
        main_frame.rowconfigure(2, weight=0)
        # 设置第四行（整理方式）按内容高度显示。
        main_frame.rowconfigure(3, weight=0)
        # 设置第五行（开始按钮）按内容高度显示。
        main_frame.rowconfigure(4, weight=0)
        # 设置第六行（日志标题）按内容高度显示。
        main_frame.rowconfigure(5, weight=0)
        # 设置第七行（日志框）占据剩余空间。
        main_frame.rowconfigure(6, weight=1)
        # 设置第八行（撤销按钮）按内容高度显示。
        main_frame.rowconfigure(7, weight=0)
        # 让内容能够随窗口宽度伸缩。
        main_frame.columnconfigure(0, weight=1)

        # 创建窗口标题。
        title_label = tk.Label(
            main_frame,
            text="文件整理助手",
            font=("Microsoft YaHei UI", 20, "bold"),
            bg=self.BACKGROUND_COLOR,
            fg=self.TEXT_COLOR,
            anchor="w",
        )
        # 把标题放在第一行。
        title_label.grid(row=0, column=0, sticky="w")

        # 创建一句简短说明，告诉用户这个程序能做什么。
        subtitle_label = tk.Label(
            main_frame,
            text="选择文件夹，按类型或修改月份自动归类；整理后可一键撤销。",
            font=("Microsoft YaHei UI", 10),
            bg=self.BACKGROUND_COLOR,
            fg=self.MUTED_COLOR,
            anchor="w",
        )
        # 把说明文字放在第二行，并在下方留出间距。
        subtitle_label.grid(row=1, column=0, sticky="w", pady=(4, 20))

        # 创建路径选择区域。
        path_frame = tk.Frame(main_frame, bg=self.BACKGROUND_COLOR)
        # 让路径区域横向铺满。
        path_frame.grid(row=2, column=0, sticky="ew")
        # 让中间的输入框能够自动伸缩。
        path_frame.columnconfigure(1, weight=1)

        # 创建“文件夹路径”标签。
        path_label = tk.Label(
            path_frame,
            text="文件夹路径",
            font=("Microsoft YaHei UI", 10, "bold"),
            bg=self.BACKGROUND_COLOR,
            fg=self.TEXT_COLOR,
        )
        # 把标签放在路径区域最左边。
        path_label.grid(row=0, column=0, sticky="w", padx=(0, 12))

        # 创建路径输入框，用户也可以手动输入路径。
        self.path_entry = ttk.Entry(
            path_frame,
            textvariable=self.folder_path,
            font=("Microsoft YaHei UI", 11),
        )
        # 让输入框占据路径区域的中间位置。
        self.path_entry.grid(row=0, column=1, sticky="ew", ipady=5)

        # 创建“浏览”按钮，点击后打开系统文件夹选择窗口。
        self.browse_button = ttk.Button(
            path_frame,
            text="浏览…",
            command=self._choose_folder,
        )
        # 把“浏览”按钮放在输入框右侧。
        self.browse_button.grid(row=0, column=2, padx=(10, 0), ipady=3)

        # 创建整理方式区域。
        mode_frame = tk.Frame(main_frame, bg=self.BACKGROUND_COLOR)
        # 让整理方式区域横向铺满，并在上方留出间距。
        mode_frame.grid(row=3, column=0, sticky="ew", pady=(14, 0))
        # 让右侧提示文字能够占据剩余宽度。
        mode_frame.columnconfigure(2, weight=1)

        # 创建“整理方式”标签。
        mode_label = tk.Label(
            mode_frame,
            text="整理方式",
            font=("Microsoft YaHei UI", 10, "bold"),
            bg=self.BACKGROUND_COLOR,
            fg=self.TEXT_COLOR,
        )
        # 把标签放在整理方式区域最左边。
        mode_label.grid(row=0, column=0, sticky="w", padx=(0, 12))

        # 创建下拉菜单，让用户选择按类型或按修改时间整理。
        self.mode_combo = ttk.Combobox(
            mode_frame,
            textvariable=self.selected_mode,
            values=[label for label, _ in MODE_OPTIONS],
            state="readonly",
            width=20,
            style="Mode.TCombobox",
        )
        # 把下拉菜单放在标签右侧。
        self.mode_combo.grid(row=0, column=1, sticky="w", ipady=3)

        # 创建一条提示，说明按修改时间整理会生成什么样的文件夹。
        mode_hint = tk.Label(
            mode_frame,
            text="按修改时间会创建如 2026-10 的月份文件夹",
            font=("Microsoft YaHei UI", 9),
            bg=self.BACKGROUND_COLOR,
            fg=self.MUTED_COLOR,
            anchor="e",
        )
        # 把提示放在下拉菜单右侧，并靠右对齐。
        mode_hint.grid(row=0, column=2, sticky="e", padx=(12, 0))

        # 创建“开始整理”按钮。
        self.start_button = tk.Button(
            main_frame,
            text="开始整理",
            command=self._start_organizing,
            font=("Microsoft YaHei UI", 14, "bold"),
            bg=self.ACCENT_COLOR,
            fg="white",
            activebackground=self.ACCENT_ACTIVE_COLOR,
            activeforeground="white",
            disabledforeground="#dbeafe",
            relief="flat",
            bd=0,
            cursor="hand2",
            pady=12,
        )
        # 让按钮横向铺满，并在上下留出间距。
        self.start_button.grid(row=4, column=0, sticky="ew", pady=(18, 20))

        # 创建“整理日志”标题。
        log_title = tk.Label(
            main_frame,
            text="整理日志",
            font=("Microsoft YaHei UI", 10, "bold"),
            bg=self.BACKGROUND_COLOR,
            fg=self.TEXT_COLOR,
            anchor="w",
        )
        # 把日志标题放在日志框上方。
        log_title.grid(row=5, column=0, sticky="w", pady=(0, 8))

        # 创建日志区域的外框。
        log_frame = tk.Frame(main_frame, bg="#d1d5db", padx=1, pady=1)
        # 让日志区域占据剩余的垂直空间。
        log_frame.grid(row=6, column=0, sticky="nsew")
        # 让文本框能够自动伸缩。
        log_frame.rowconfigure(0, weight=1)
        log_frame.columnconfigure(0, weight=1)

        # 创建显示整理过程的文本框。
        self.log_text = tk.Text(
            log_frame,
            wrap="word",
            font=("Microsoft YaHei UI", 10),
            bg="#ffffff",
            fg=self.TEXT_COLOR,
            relief="flat",
            bd=0,
            padx=12,
            pady=10,
            state="disabled",
        )
        # 把日志文本框放在外框内。
        self.log_text.grid(row=0, column=0, sticky="nsew")

        # 创建日志滚动条，方便查看较长的记录。
        scrollbar = ttk.Scrollbar(
            log_frame,
            orient="vertical",
            command=self.log_text.yview,
        )
        # 把滚动条放在文本框右侧。
        scrollbar.grid(row=0, column=1, sticky="ns")
        # 让文本框的滚动操作同步到滚动条。
        self.log_text.configure(yscrollcommand=scrollbar.set)

        # 创建“撤销上一步”按钮，放在界面最下方。
        self.undo_button = tk.Button(
            main_frame,
            text="撤销上一步",
            command=self._start_undo,
            font=("Microsoft YaHei UI", 11, "bold"),
            bg="#ffffff",
            fg=self.ACCENT_COLOR,
            activebackground=self.SECONDARY_ACTIVE_COLOR,
            activeforeground=self.ACCENT_ACTIVE_COLOR,
            disabledforeground="#9ca3af",
            relief="solid",
            bd=1,
            highlightthickness=0,
            cursor="hand2",
            pady=8,
            state="disabled",
        )
        # 让撤销按钮横向铺满，并与日志区域拉开一点距离。
        self.undo_button.grid(row=7, column=0, sticky="ew", pady=(14, 0))

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
        self.mode_combo.configure(state="readonly")
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
        # 记录用户选择的模式名称，方便日志阅读。
        mode_label = self.selected_mode.get()

        # 清空上一次的日志，准备显示本次整理过程。
        self._clear_log()
        # 记录本次整理的文件夹和整理方式。
        self._append_log(f"开始整理：{source_directory}")
        self._append_log(f"整理方式：{mode_label}")
        # 标记程序正在执行后台任务，并禁用相关控件。
        self._set_busy(True, "正在整理…")

        # 创建后台线程，把耗时的文件移动操作放到界面线程之外。
        worker = threading.Thread(
            target=self._organize_in_background,
            args=(source_directory, mode),
            daemon=True,
        )
        # 启动后台线程。
        worker.start()
        # 开始定期检查后台线程发来的日志。
        self.root.after(100, self._process_messages)

    def _organize_in_background(self, source_directory: Path, mode: str) -> None:
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
                },
                log_callback=send_log,
                mode=mode,
                move_records=move_records,
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
    """创建主窗口并进入 tkinter 事件循环。"""

    # 创建 tkinter 主窗口。
    root = tk.Tk()
    # 设置窗口左上角图标；找不到图标时继续正常启动程序。
    try:
        root.iconbitmap("app_icon.ico")
    except (tk.TclError, OSError):
        pass
    # 使用较新的系统主题，让按钮和滚动条更接近系统样式。
    try:
        ttk.Style().theme_use("vista")
    except tk.TclError:
        # 如果当前系统没有 vista 主题，就继续使用默认主题。
        pass
    # 创建程序界面。
    OrganizerApp(root)
    # 启动事件循环，窗口会一直显示，直到用户关闭它。
    root.mainloop()


# 只有直接运行这个文件时才启动图形界面。
if __name__ == "__main__":
    main()
