"""Canonical services, independent of reference repositories and their aliases."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

Protocol = Literal["chat", "messages", "responses", "gemini", "vertex", "bedrock", "pi-messages", "classifier", "multi", "mistral"]


@dataclass(frozen=True)
class ProviderDefinition:
    id: str
    name: str
    base_url: str
    protocol: Protocol = "chat"
    auth: tuple[str, ...] = ("api_key",)
    plan: str = "api"
    catalog: str = "/models"
    adapter: str = "configured"
    implemented: bool = True
    key_env: str = ""

    @property
    def identity(self) -> tuple[str, Protocol, tuple[str, ...], str]:
        return self.base_url.rstrip("/"), self.protocol, self.auth, self.plan


CANONICAL_PROVIDERS: tuple[ProviderDefinition, ...] = (
    ProviderDefinition('amazon-bedrock', 'Amazon Bedrock', 'https://bedrock-runtime.{region}.amazonaws.com', protocol='bedrock', auth=('aws', 'bearer'), plan='api', catalog='AWS ListFoundationModels / configured models', adapter='dedicated', key_env=''),
    ProviderDefinition('ant-ling', 'Ant Ling', 'https://api.ant-ling.com/v1', protocol='chat', auth=('api_key',), plan='api', catalog='/models', adapter='configured', key_env='ANT_LING_API_KEY'),
    ProviderDefinition('anthropic', 'Anthropic', 'https://api.anthropic.com/v1', protocol='messages', auth=('api_key', 'oauth'), plan='api', catalog='/models', adapter='configured', key_env='ANTHROPIC_API_KEY'),
    ProviderDefinition('azure', 'Azure OpenAI', 'https://{resource}.openai.azure.com/openai/v1', protocol='multi', auth=('api_key',), plan='api', catalog='configured deployments', adapter='dedicated', key_env='AZURE_OPENAI_API_KEY'),
    ProviderDefinition('baseten', 'Baseten', 'https://inference.baseten.co/v1', protocol='chat', auth=('api_key',), plan='api', catalog='/models', adapter='configured', key_env='BASETEN_API_KEY'),
    ProviderDefinition('cerebras', 'Cerebras', 'https://api.cerebras.ai/v1', protocol='chat', auth=('api_key',), plan='api', catalog='/models', adapter='configured', key_env='CEREBRAS_API_KEY'),
    ProviderDefinition('cloudflare-ai-gateway', 'Cloudflare AI Gateway', 'https://gateway.ai.cloudflare.com/v1/{account_id}/{gateway_id}/compat', protocol='multi', auth=('api_key',), plan='api', catalog='/models', adapter='dedicated', key_env='CLOUDFLARE_API_KEY'),
    ProviderDefinition('cloudflare-workers-ai', 'Cloudflare Workers AI', 'https://api.cloudflare.com/client/v4/accounts/{account_id}/ai/v1', protocol='chat', auth=('api_key',), plan='api', catalog='/models', adapter='dedicated', key_env='CLOUDFLARE_API_KEY'),
    ProviderDefinition('deepseek', 'DeepSeek', 'https://api.deepseek.com/v1', protocol='chat', auth=('api_key',), plan='api', catalog='/models', adapter='configured', key_env='DEEPSEEK_API_KEY'),
    ProviderDefinition('fireworks', 'Fireworks', 'https://api.fireworks.ai/inference/v1', protocol='multi', auth=('api_key',), plan='api', catalog='/models', adapter='configured', key_env='FIREWORKS_API_KEY'),
    ProviderDefinition('github-copilot', 'GitHub Copilot', 'https://api.individual.githubcopilot.com', protocol='multi', auth=('api_key', 'oauth'), plan='subscription', catalog='/models', adapter='configured', key_env='COPILOT_GITHUB_TOKEN'),
    ProviderDefinition('google', 'Google Gemini API', 'https://generativelanguage.googleapis.com/v1beta', protocol='gemini', auth=('api_key',), plan='api', catalog='/models', adapter='dedicated', key_env='GEMINI_API_KEY'),
    ProviderDefinition('google-vertex', 'Google Vertex AI', 'https://{location}-aiplatform.googleapis.com', protocol='vertex', auth=('api_key', 'google_adc'), plan='api', catalog='configured models / publisher catalog', adapter='dedicated', key_env=''),
    ProviderDefinition('groq', 'Groq', 'https://api.groq.com/openai/v1', protocol='chat', auth=('api_key',), plan='api', catalog='/models', adapter='configured', key_env='GROQ_API_KEY'),
    ProviderDefinition('huggingface', 'Hugging Face', 'https://router.huggingface.co/v1', protocol='chat', auth=('api_key',), plan='api', catalog='/models', adapter='configured', key_env='HF_TOKEN'),
    ProviderDefinition('kimi-coding', 'Kimi Coding', 'https://api.kimi.com/coding/v1', protocol='chat', auth=('api_key', 'oauth'), plan='coding', catalog='/models', adapter='configured', key_env='KIMI_API_KEY'),
    ProviderDefinition('meta', 'Meta', 'https://api.meta.ai/v1', protocol='responses', auth=('api_key', 'oauth'), plan='api', catalog='/models', adapter='configured', key_env='META_API_KEY'),
    ProviderDefinition('minimax-api', 'MiniMax API', 'https://api.minimax.io/anthropic/v1', protocol='messages', auth=('api_key',), plan='api', catalog='/models', adapter='configured', key_env='MINIMAX_API_KEY'),
    ProviderDefinition('minimax-cn', 'MiniMax API CN', 'https://api.minimaxi.com/anthropic/v1', protocol='messages', auth=('api_key',), plan='api', catalog='/models', adapter='configured', key_env='MINIMAX_CN_API_KEY'),
    ProviderDefinition('mistral', 'Mistral', 'https://api.mistral.ai/v1', protocol='mistral', auth=('api_key',), plan='api', catalog='/models', adapter='configured', key_env='MISTRAL_API_KEY'),
    ProviderDefinition('moonshotai', 'Moonshot', 'https://api.moonshot.ai/v1', protocol='chat', auth=('api_key',), plan='api', catalog='/models', adapter='configured', key_env='MOONSHOT_API_KEY'),
    ProviderDefinition('moonshotai-cn', 'Moonshot CN', 'https://api.moonshot.cn/v1', protocol='chat', auth=('api_key',), plan='api', catalog='/models', adapter='configured', key_env='MOONSHOT_API_KEY'),
    ProviderDefinition('nvidia', 'NVIDIA NIM', 'https://integrate.api.nvidia.com/v1', protocol='chat', auth=('api_key',), plan='api', catalog='/models', adapter='dedicated', key_env='NVIDIA_API_KEY'),
    ProviderDefinition('openai', 'OpenAI', 'https://api.openai.com/v1', protocol='responses', auth=('api_key', 'oauth'), plan='api', catalog='/models', adapter='configured', key_env='OPENAI_API_KEY'),
    ProviderDefinition('codex', 'Codex (ChatGPT OAuth)', 'https://chatgpt.com/backend-api/codex', protocol='responses', auth=('oauth',), plan='subscription', catalog='/models?client_version=0.107.0', adapter='configured', key_env=''),
    ProviderDefinition('opencode', 'OpenCode Zen', 'https://opencode.ai/inference/v1', protocol='multi', auth=('anonymous', 'api_key'), plan='api', catalog='/models', adapter='dedicated', key_env='OPENCODE_API_KEY'),
    ProviderDefinition('opencode-go', 'OpenCode Go', 'https://opencode.ai/zen/go/v1', protocol='multi', auth=('api_key',), plan='go', catalog='/models', adapter='configured', key_env='OPENCODE_API_KEY'),
    ProviderDefinition('openrouter', 'OpenRouter', 'https://openrouter.ai/api/v1', protocol='multi', auth=('api_key', 'oauth'), plan='api', catalog='/models/user (fallback /models)', adapter='configured', key_env='OPENROUTER_API_KEY'),
    ProviderDefinition('qwen-token-plan', 'Qwen Token Plan', 'https://token-plan.ap-southeast-1.maas.aliyuncs.com/compatible-mode/v1', protocol='chat', auth=('api_key',), plan='token-plan', catalog='/models', adapter='configured', key_env='QWEN_TOKEN_PLAN_API_KEY'),
    ProviderDefinition('qwen-token-plan-cn', 'Qwen Token Plan CN', 'https://token-plan.cn-beijing.maas.aliyuncs.com/compatible-mode/v1', protocol='chat', auth=('api_key',), plan='token-plan-cn', catalog='/models', adapter='configured', key_env='QWEN_TOKEN_PLAN_CN_API_KEY'),
    ProviderDefinition('qwen-token-plan-individual', 'Qwen Token Plan Individual', 'https://token-plan.ap-southeast-1.maas.aliyuncs.com/compatible-mode/v1', protocol='chat', auth=('api_key',), plan='individual', catalog='/models', adapter='configured', key_env='QWEN_TOKEN_PLAN_API_KEY'),
    ProviderDefinition('radius', 'Radius', 'https://radius.pi.dev', protocol='pi-messages', auth=('api_key', 'oauth'), plan='api', catalog='/v1/config', adapter='dedicated', key_env='RADIUS_API_KEY'),
    ProviderDefinition('together', 'Together', 'https://api.together.ai/v1', protocol='chat', auth=('api_key',), plan='api', catalog='/models', adapter='configured', key_env='TOGETHER_API_KEY'),
    ProviderDefinition('typesafe', 'TypeSafe', 'https://api.typesafe.ai/v1', protocol='classifier', auth=('api_key',), plan='api', catalog='configured classifiers', adapter='dedicated', key_env='TYPESAFE_API_KEY'),
    ProviderDefinition('vercel-ai-gateway', 'Vercel AI Gateway', 'https://ai-gateway.vercel.sh/v1', protocol='messages', auth=('api_key',), plan='api', catalog='/models', adapter='configured', key_env='AI_GATEWAY_API_KEY'),
    ProviderDefinition('xai', 'xAI', 'https://api.x.ai/v1', protocol='responses', auth=('api_key', 'oauth'), plan='api', catalog='/models', adapter='configured', key_env='XAI_API_KEY'),
    ProviderDefinition('xiaomi', 'Xiaomi MiMo', 'https://api.xiaomimimo.com/v1', protocol='chat', auth=('api_key',), plan='api', catalog='/models', adapter='configured', key_env='XIAOMI_API_KEY'),
    ProviderDefinition('xiaomi-token-plan-ams', 'Xiaomi Token Plan AMS', 'https://token-plan-ams.xiaomimimo.com/v1', protocol='chat', auth=('api_key',), plan='ams', catalog='/models', adapter='configured', key_env='XIAOMI_TOKEN_PLAN_AMS_API_KEY'),
    ProviderDefinition('xiaomi-token-plan-cn', 'Xiaomi Token Plan CN', 'https://token-plan-cn.xiaomimimo.com/v1', protocol='chat', auth=('api_key',), plan='cn', catalog='/models', adapter='configured', key_env='XIAOMI_TOKEN_PLAN_CN_API_KEY'),
    ProviderDefinition('xiaomi-token-plan-sgp', 'Xiaomi Token Plan SGP', 'https://token-plan-sgp.xiaomimimo.com/v1', protocol='chat', auth=('api_key',), plan='sgp', catalog='/models', adapter='configured', key_env='XIAOMI_TOKEN_PLAN_SGP_API_KEY'),
    ProviderDefinition('zai', 'Z.AI Coding', 'https://api.z.ai/api/coding/paas/v4', protocol='chat', auth=('api_key',), plan='coding', catalog='/models', adapter='configured', key_env='ZAI_API_KEY'),
    ProviderDefinition('zai-coding-cn', 'Z.AI Coding CN', 'https://open.bigmodel.cn/api/coding/paas/v4', protocol='chat', auth=('api_key',), plan='coding-cn', catalog='/models', adapter='configured', key_env='ZAI_CODING_CN_API_KEY'),
    ProviderDefinition('cline-pass', 'ClinePass', 'https://api.cline.bot/api/v1', protocol='chat', auth=('api_key',), plan='subscription', catalog='/ai/cline/recommended-models (clinePass)', adapter='configured', key_env='CLINE_API_KEY'),
    ProviderDefinition('alibaba-cloud', 'Alibaba Cloud', 'https://dashscope-intl.aliyuncs.com/compatible-mode/v1', protocol='chat', auth=('api_key',), plan='api', catalog='/api/v1/models (DashScope paginated)', adapter='configured', key_env='ALIBABA_CLOUD_API_KEY'),
    ProviderDefinition('qwen-coding-plan-intl', 'QwenCloud Coding Plan', 'https://coding-intl.dashscope.aliyuncs.com/v1', protocol='chat', auth=('api_key',), plan='coding', catalog='/models', adapter='configured', key_env='QWENCLOUD_CODING_API_KEY'),
    ProviderDefinition('siliconflow', 'SiliconFlow', 'https://api.siliconflow.com/v1', protocol='chat', auth=('api_key',), plan='api', catalog='/models?sub_type=chat', adapter='configured', key_env='SILICONFLOW_API_KEY'),
    ProviderDefinition('nebius', 'Nebius Token Factory', 'https://api.tokenfactory.nebius.com/v1', protocol='chat', auth=('api_key',), plan='api', catalog='/models?verbose=true', adapter='configured', key_env='NEBIUS_API_KEY'),
    ProviderDefinition('chutes', 'Chutes', 'https://llm.chutes.ai/v1', protocol='chat', auth=('api_key',), plan='api', catalog='/models', adapter='configured', key_env='CHUTES_API_KEY'),
    ProviderDefinition('featherless', 'Featherless AI', 'https://api.featherless.ai/v1', protocol='chat', auth=('api_key',), plan='api', catalog='/models (paginated, current plan)', adapter='configured', key_env='FEATHERLESS_API_KEY'),
    ProviderDefinition('wandb', 'W&B Inference', 'https://api.inference.wandb.ai/v1', protocol='chat', auth=('api_key',), plan='api', catalog='/models', adapter='configured', key_env='WANDB_API_KEY'),
    ProviderDefinition('mistral-codestral', 'Mistral Codestral', 'https://codestral.mistral.ai/v1', protocol='chat', auth=('api_key',), plan='coding', catalog='/models', adapter='configured', key_env='CODESTRAL_API_KEY'),
    ProviderDefinition('bedrock-mantle', 'Amazon Bedrock', 'https://bedrock-mantle.us-east-1.api.aws/v1', protocol='chat', auth=('api_key',), plan='api', catalog='/models', adapter='configured', key_env='AWS_BEARER_TOKEN_BEDROCK'),
    ProviderDefinition('cohere', 'Cohere', 'https://api.cohere.ai/compatibility/v1', protocol='chat', auth=('api_key',), plan='api', catalog='/models', adapter='configured', key_env='COHERE_API_KEY'),
    ProviderDefinition('wafer', 'Wafer', 'https://pass.wafer.ai/v1', protocol='chat', auth=('api_key',), plan='api', catalog='/models', adapter='configured', key_env='WAFER_API_KEY'),
    ProviderDefinition('minimax-chat', 'MiniMax', 'https://api.minimax.io/v1', protocol='chat', auth=('api_key',), plan='api', catalog='/models', adapter='configured', key_env='MINIMAX_API_KEY'),
    ProviderDefinition('zai-api', 'Z.ai API', 'https://api.z.ai/api/paas/v4', protocol='chat', auth=('api_key',), plan='api', catalog='/models', adapter='configured', key_env='ZAI_API_KEY'),
    ProviderDefinition('nararoute', 'NaraRoute', 'https://router.bynara.id/v1', protocol='chat', auth=('api_key',), plan='api', catalog='/models', adapter='configured', key_env='NARAROUTE_API_KEY'),
    ProviderDefinition('poolside', 'Poolside AI', 'https://inference.poolside.ai/v1', protocol='chat', auth=('api_key',), plan='api', catalog='/models', adapter='configured', key_env='POOLSIDE_API_KEY'),
    ProviderDefinition('scaleway', 'Scaleway', 'https://api.scaleway.ai/v1', protocol='chat', auth=('api_key',), plan='api', catalog='/models', adapter='configured', key_env='SCW_SECRET_KEY'),
    ProviderDefinition('lightning', 'Lightning AI', 'https://lightning.ai/api/v1', protocol='chat', auth=('api_key',), plan='api', catalog='/models', adapter='configured', key_env='LIGHTNING_API_KEY'),
    ProviderDefinition('experiential', 'Experiential Labs', 'https://api.experientiallabs.ai/v1', protocol='chat', auth=('api_key',), plan='api', catalog='/models', adapter='configured', key_env='EXPLABS_API_KEY'),
    ProviderDefinition('cheaperinference', 'Cheaper Inference', 'https://api.cheaperinference.com/v1', protocol='chat', auth=('api_key',), plan='api', catalog='/models', adapter='configured', key_env='CHEAPER_INFERENCE_API_KEY'),
    ProviderDefinition('atlascloud', 'Atlas Cloud', 'https://api.atlascloud.ai/v1', protocol='chat', auth=('api_key',), plan='api', catalog='/models', adapter='configured', key_env='ATLASCLOUD_API_KEY'),
    ProviderDefinition('opper', 'Opper', 'https://api.opper.ai/v3/compat', protocol='chat', auth=('api_key',), plan='api', catalog='/models', adapter='configured', key_env='OPPER_API_KEY'),
    ProviderDefinition('lmstudio', 'LM Studio', 'http://localhost:1234/v1', protocol='chat', auth=('none', 'api_key'), plan='local', catalog='/models', adapter='local', implemented=True, key_env=''),
    ProviderDefinition('llamacpp', 'LLaMA.cpp', 'http://localhost:8080/v1', protocol='chat', auth=('none', 'api_key'), plan='local', catalog='/models', adapter='local', implemented=True, key_env=''),
    ProviderDefinition('ollama', 'Ollama', 'http://localhost:11434/v1', protocol='chat', auth=('none', 'api_key'), plan='local', catalog='/models', adapter='local', implemented=True, key_env=''),
)
PROVIDER_BY_ID = {definition.id: definition for definition in CANONICAL_PROVIDERS}
REGULAR_PROVIDER_IDS = frozenset(d.id for d in CANONICAL_PROVIDERS if d.implemented and d.adapter == "configured")


def definition_for(provider_id: str) -> ProviderDefinition | None:
    return PROVIDER_BY_ID.get(provider_id)
