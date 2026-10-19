# 导入 shutil 模块，用来移动文件。
import shutil
# 从 pathlib 模块导入 Path，用来更方便地处理文件路径。
from pathlib import Path

# 在程序开始时打印提示，告诉用户整理任务即将开始。
print("已开始整理，请稍候")

# 记录当前工作目录，后续只整理这个目录中的文件。
SOURCE_DIRECTORY = Path.cwd()

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


# 定义函数，根据文件扩展名返回文件所属的分类名称。
def get_category(file_path: Path) -> str:
    # 获取文件扩展名并转换为小写，让 .JPG 和 .jpg 都能被正确识别。
    suffix = file_path.suffix.lower()
    # 依次检查分类规则中的每个分类。
    for category, extensions in CATEGORY_RULES.items():
        # 如果扩展名属于当前分类，就返回对应的文件夹名称。
        if suffix in extensions:
            # 找到匹配项后立即结束函数，不再检查后续分类。
            return category
    # 所有分类都不匹配时，返回 Others，表示未知格式。
    return "Others"


# 定义函数，为目标文件生成一个不会覆盖已有文件的路径。
def build_destination(target_directory: Path, file_name: str) -> Path:
    # 先拼接出最直接的目标路径。
    destination = target_directory / file_name
    # 如果目标位置没有同名文件，就直接使用这个路径。
    if not destination.exists():
        # 返回可以直接使用的目标路径。
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
            # 返回可用的候选路径。
            return candidate
        # 当前序号已占用，准备检查下一个序号。
        index += 1


# 定义主函数，完成遍历、分类和移动文件的操作。
def organize_current_directory() -> None:
    # 初始化成功移动的文件数量。
    moved_count = 0
    # 初始化移动失败的文件数量。
    failed_count = 0
    # 获取当前脚本自身的绝对路径，避免整理时把脚本自己移走。
    current_script = Path(__file__).resolve()
    # 遍历当前工作目录中的每一个项目。
    for file_path in SOURCE_DIRECTORY.iterdir():
        # 如果当前项目不是文件，就跳过目录、快捷方式等非普通文件。
        if not file_path.is_file():
            # 继续检查下一个项目。
            continue
        # 如果当前项目就是本脚本，就跳过它，保证程序文件留在原处。
        if file_path.resolve() == current_script:
            # 继续检查下一个项目。
            continue
        # 根据扩展名判断当前文件属于哪个分类。
        category = get_category(file_path)
        # 拼接分类文件夹的完整路径。
        target_directory = SOURCE_DIRECTORY / category
        # 使用异常处理，避免单个文件失败后中断整个整理任务。
        try:
            # 创建分类文件夹；如果文件夹已经存在，不会报错。
            target_directory.mkdir(parents=True, exist_ok=True)
            # 计算不会覆盖已有文件的目标路径。
            destination = build_destination(target_directory, file_path.name)
            # 把当前文件从原位置移动到分类文件夹中。
            shutil.move(str(file_path), str(destination))
            # 输出本次移动结果，方便用户核对。
            print(f"已移动：{file_path.name} -> {category}/")
            # 成功移动后，把成功数量加一。
            moved_count += 1
        # 捕获文件移动过程中可能出现的系统或权限错误。
        except OSError as error:
            # 输出失败原因，但不终止其他文件的整理。
            print(f"移动失败：{file_path.name}（{error}）")
            # 移动失败后，把失败数量加一。
            failed_count += 1
    # 所有文件处理完成后，输出简短的结果摘要。
    print(f"整理完成：成功移动 {moved_count} 个文件，失败 {failed_count} 个文件。")


# 只有直接运行这个脚本时才执行整理，作为模块导入时不会自动移动文件。
if __name__ == "__main__":
    # 调用主函数，开始整理当前工作目录。
    organize_current_directory()
    input("按回车键退出")
