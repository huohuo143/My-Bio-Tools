"""Cloud LLM provider presets for My Bio Tools.

Only non-secret connection metadata belongs here. API keys remain in the
current Streamlit session and must never be written to the preference file,
reports, logs, or release artifacts.
"""

from __future__ import annotations

from dataclasses import dataclass


PROVIDER_OPENAI_COMPATIBLE = "openai_compatible"
PROVIDER_DEEPSEEK = "deepseek_api"
PROVIDER_DOUBAO = "doubao_ark_api"
PROVIDER_ZHIPU = "zhipu_glm_api"
PROVIDER_QWEN = "qwen_dashscope_api"
PROVIDER_CHATANYWHERE = "chatanywhere_api"


@dataclass(frozen=True)
class CloudProviderPreset:
    provider_id: str
    label: str
    preference_prefix: str
    default_base_url: str
    default_model: str
    model_placeholder: str
    api_docs_url: str
    json_object_mode: bool


CLOUD_PROVIDER_PRESETS: tuple[CloudProviderPreset, ...] = (
    CloudProviderPreset(
        PROVIDER_DEEPSEEK,
        "DeepSeek API",
        "deepseek",
        "https://api.deepseek.com",
        "deepseek-v4-pro",
        "例：deepseek-v4-pro 或 deepseek-v4-flash",
        "https://api-docs.deepseek.com/zh-cn/",
        True,
    ),
    CloudProviderPreset(
        PROVIDER_DOUBAO,
        "豆包 API（火山方舟）",
        "doubao",
        "https://ark.cn-beijing.volces.com/api/v3",
        "doubao-seed-2-0-lite-260215",
        "填写火山方舟控制台显示的模型或接入点 ID",
        "https://www.volcengine.com/docs/82379/1795150",
        False,
    ),
    CloudProviderPreset(
        PROVIDER_ZHIPU,
        "智谱 GLM API",
        "zhipu",
        "https://open.bigmodel.cn/api/paas/v4",
        "glm-5.2",
        "例：glm-5.2",
        "https://docs.bigmodel.cn/api-reference/模型-api/对话补全",
        False,
    ),
    CloudProviderPreset(
        PROVIDER_QWEN,
        "通义千问 API（阿里云百炼）",
        "qwen",
        "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "qwen-plus",
        "例：qwen-plus；也可填写工作空间专属模型 ID",
        "https://help.aliyun.com/zh/model-studio/first-api-call-to-qwen",
        True,
    ),
    CloudProviderPreset(
        PROVIDER_CHATANYWHERE,
        "ChatAnywhere API",
        "chatanywhere",
        "https://api.chatanywhere.tech/v1",
        "gpt-4o-mini",
        "填写 ChatAnywhere 当前账号可用的模型 ID",
        "https://docs.chatanywhere.tech/api-92222076",
        True,
    ),
    CloudProviderPreset(
        PROVIDER_OPENAI_COMPATIBLE,
        "OpenAI / 自定义兼容 API",
        "openai",
        "https://api.openai.com/v1",
        "",
        "例：gpt-5.2，或自建服务的模型 ID",
        "https://platform.openai.com/docs/api-reference/chat",
        True,
    ),
)

CLOUD_PROVIDER_BY_ID = {
    item.provider_id: item for item in CLOUD_PROVIDER_PRESETS
}
CLOUD_API_PROVIDERS = frozenset(CLOUD_PROVIDER_BY_ID)


def cloud_provider(provider: str) -> CloudProviderPreset:
    try:
        return CLOUD_PROVIDER_BY_ID[provider]
    except KeyError as exc:
        raise ValueError(f"不支持的云端大模型提供方：{provider}") from exc


def cloud_provider_label(provider: str) -> str:
    return cloud_provider(provider).label


def is_cloud_api_provider(provider: str) -> bool:
    return provider in CLOUD_API_PROVIDERS


def chat_completions_url(base_url: str) -> str:
    normalized = base_url.strip().rstrip("/")
    if not normalized:
        raise ValueError("模型服务地址不能为空。")
    return normalized if normalized.endswith("/chat/completions") else normalized + "/chat/completions"


def cloud_preference_keys(provider: str) -> tuple[str, str]:
    prefix = cloud_provider(provider).preference_prefix
    return f"{prefix}_base_url", f"{prefix}_model"


__all__ = [
    "CLOUD_API_PROVIDERS",
    "CLOUD_PROVIDER_BY_ID",
    "CLOUD_PROVIDER_PRESETS",
    "CloudProviderPreset",
    "PROVIDER_CHATANYWHERE",
    "PROVIDER_DEEPSEEK",
    "PROVIDER_DOUBAO",
    "PROVIDER_OPENAI_COMPATIBLE",
    "PROVIDER_QWEN",
    "PROVIDER_ZHIPU",
    "chat_completions_url",
    "cloud_preference_keys",
    "cloud_provider",
    "cloud_provider_label",
    "is_cloud_api_provider",
]
