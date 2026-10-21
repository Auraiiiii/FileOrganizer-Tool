# -*- coding: utf-8 -*-
"""使用 DeepSeek 为文本文件生成简短摘要的独立模块。

这个模块只负责读取配置、创建 OpenAI 兼容客户端以及调用摘要接口。
文件读取、文件名清洗、移动失败回退等界面相关逻辑由 gui_organizer.py 处理。
这样可以让 AI 模块保持独立，也方便以后替换模型或测试摘要逻辑。
"""

# 延迟解析类型注解，兼容较旧版本的 Python。
from __future__ import annotations

# 导入 os，用来从环境变量中读取 API Key。
import os
# 导入 Path，用来定位与当前模块放在同一目录下的 .env 文件。
from pathlib import Path

# 明确记录 DeepSeek 的接口地址，避免以后误用到 OpenAI 官方地址。
DEEPSEEK_BASE_URL = "https://api.deepseek.com"
# DeepSeek 当前用于通用对话和摘要的模型名称。
DEEPSEEK_MODEL = "deepseek-chat"

# 使用模块所在目录定位 .env，确保从其他工作目录启动 GUI 时也能读到配置。
ENV_FILE = Path(__file__).resolve().with_name(".env")

# 第三方依赖采用容错导入。缺少某个包时仍允许本模块被导入，
# 这样 GUI 就能在日志中显示具体缺少哪个包，而不是只看到一个导入失败。
load_dotenv = None
OpenAI = None
_dependency_errors: list[str] = []

try:
    # 导入 dotenv 提供的配置加载函数。
    from dotenv import load_dotenv
except ImportError as error:
    _dependency_errors.append(
        f"python-dotenv 未安装或无法导入（{type(error).__name__}: {error}）"
    )

try:
    # 导入 OpenAI 客户端；DeepSeek 提供与 OpenAI SDK 兼容的接口。
    from openai import OpenAI
except ImportError as error:
    _dependency_errors.append(
        f"openai 未安装或无法导入（{type(error).__name__}: {error}）"
    )

# 系统提示词同时完成角色设定、长度限制和格式要求。
SYSTEM_PROMPT = (
    "你是一个文件整理助手，请用不超过 10 个字总结这段文字的核心内容，"
    "不要标点，不要废话"
)

# 保存 .env 的加载错误，诊断日志会在不泄露密钥的前提下显示具体原因。
_dotenv_load_error: str | None = None

# 只有依赖可用时才读取 .env；加载时不会覆盖操作系统中已有的同名变量。
if load_dotenv is not None:
    try:
        load_dotenv(dotenv_path=ENV_FILE, override=False)
    except Exception as error:
        _dotenv_load_error = (
            f"{type(error).__name__}: {str(error).strip() or repr(error)}"
        )

# 从环境变量中读取密钥；没有配置时保留 None，交由 summarize_text 给出清晰错误。
_api_key = os.getenv("DEEPSEEK_API_KEY")

# 客户端声明为可能为空，便于在缺少密钥时仍然安全导入本模块。
client: OpenAI | None = None

# 保存客户端初始化错误，方便调用方在日志中显示具体配置问题。
_client_init_error: str | None = None

# 只有依赖和密钥都可用时才创建客户端。
if OpenAI is not None and _api_key:
    try:
        client = OpenAI(
            api_key=_api_key,
            base_url=DEEPSEEK_BASE_URL,
            # 限制单次请求的等待时间，防止无网络时后台任务长时间挂起。
            timeout=30.0,
            # 只重试一次，兼顾偶发网络波动和界面响应速度。
            max_retries=1,
        )
    except Exception as error:
        _client_init_error = (
            f"{type(error).__name__}: {str(error).strip() or repr(error)}"
        )


def get_ai_diagnostics() -> str:
    """返回不含密钥内容的 AI 配置诊断信息。"""

    # 只报告 .env 是否存在以及 Key 是否读取成功，绝不输出密钥本身。
    env_state = "存在" if ENV_FILE.is_file() else "不存在"
    if _api_key:
        key_state = "已读取"
    elif load_dotenv is None:
        key_state = "未读取（python-dotenv 不可用）"
    elif not ENV_FILE.is_file():
        key_state = "未找到（.env 不存在）"
    else:
        key_state = "未找到（已读取 .env）"
    client_state = "已初始化" if client is not None else "未初始化"
    diagnostics = [
        f".env={ENV_FILE}（{env_state}）",
        f"DEEPSEEK_API_KEY={key_state}",
        f"OpenAI 客户端={client_state}",
    ]

    # 把依赖、.env 加载和客户端初始化错误一并写进诊断文本。
    diagnostics.extend(_dependency_errors)
    if _dotenv_load_error:
        diagnostics.append(f".env 加载失败（{_dotenv_load_error}）")
    if _client_init_error:
        diagnostics.append(f"客户端初始化失败（{_client_init_error}）")

    return "；".join(diagnostics)


def summarize_text(text: str) -> str:
    """调用 DeepSeek 返回文本摘要。

    Args:
        text: 需要总结的文本。调用方通常只传入文件开头的一小段内容。

    Returns:
        模型返回的摘要字符串，已去掉首尾空白并合并多余换行。

    Raises:
        RuntimeError: 没有配置 DEEPSEEK_API_KEY 时抛出。
        ValueError: 输入为空或模型返回空摘要时抛出。
        其他异常: OpenAI SDK 会把网络错误、鉴权失败、余额不足等向上抛出，
            由 GUI 捕获并回退到普通整理流程。
    """

    # 没有有效摘要内容时不发起请求，减少无意义的网络调用和费用。
    if not text or not text.strip():
        raise ValueError("没有可供 AI 总结的文本内容")

    # 缺少密钥通常是配置问题；错误中包含 .env 绝对路径和具体依赖状态。
    if client is None:
        raise RuntimeError(
            "AI 客户端不可用，请检查依赖、.env 和 DEEPSEEK_API_KEY。"
            f"诊断信息：{get_ai_diagnostics()}"
        )

    # 调用 DeepSeek 的聊天补全接口。
    response = client.chat.completions.create(
        model=DEEPSEEK_MODEL,
        messages=[
            # 系统消息负责约束模型输出格式和最大字数。
            {"role": "system", "content": SYSTEM_PROMPT},
            # 用户消息只放待总结文本，避免把文件路径等无关信息发送给模型。
            {"role": "user", "content": text},
        ],
        # 摘要任务需要稳定结果，因此使用确定性较高的温度。
        temperature=0,
        # 限制输出 token 数量，避免模型违反提示词后返回大段文字。
        max_tokens=32,
    )

    # 兼容 SDK 返回 content 为 None 的情况，再进行首尾空白清理。
    content = response.choices[0].message.content
    summary = " ".join((content or "").split()).strip()

    # 空摘要不能用于命名，把它当作 AI 处理失败并交给 GUI 回退。
    if not summary:
        raise ValueError("AI 返回了空摘要")

    # 返回未经文件系统清洗的原始摘要，命名规则由 GUI 层统一处理。
    return summary
