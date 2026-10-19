"""Source inventories are migration evidence, never runtime construction input."""

from dataclasses import dataclass

from hoshimi.core.provider_definitions import PROVIDER_BY_ID, Protocol


@dataclass(frozen=True)
class ProviderSource:
    source: str
    source_id: str
    id: str
    name: str
    base_url: str
    protocol: Protocol
    auth: tuple[str, ...]
    plan: str
    catalog: str
    key_env: str = ""
    notes: str = ""

    @property
    def coverage(self) -> str:
        if self.id == "agg":
            return "agg"
        definition = PROVIDER_BY_ID.get(self.id)
        if definition is not None and not definition.implemented:
            return "planned"
        if "pending" in self.notes:
            return "partial"
        return "native"

    @property
    def identity(self) -> tuple[str, Protocol, tuple[str, ...], str]:
        return self.base_url.rstrip("/"), self.protocol, self.auth, self.plan


SOURCE_PROVIDERS: tuple[ProviderSource, ...] = (
    ProviderSource('pi', 'amazon-bedrock', 'amazon-bedrock', 'Amazon Bedrock', 'https://bedrock-runtime.{region}.amazonaws.com', 'bedrock', ('aws', 'bearer'), 'api', 'AWS ListFoundationModels / configured models', '', notes=''),
    ProviderSource('pi', 'ant-ling', 'ant-ling', 'Ant Ling', 'https://api.ant-ling.com/v1', 'chat', ('api_key',), 'api', '/models', 'ANT_LING_API_KEY', notes=''),
    ProviderSource('pi', 'anthropic', 'anthropic', 'Anthropic', 'https://api.anthropic.com/v1', 'messages', ('api_key', 'oauth'), 'api', '/models', 'ANTHROPIC_API_KEY', notes=''),
    ProviderSource('pi', 'azure', 'azure', 'Azure OpenAI', 'https://{resource}.openai.azure.com/openai/v1', 'multi', ('api_key',), 'api', 'configured deployments', 'AZURE_OPENAI_API_KEY', notes=''),
    ProviderSource('pi', 'baseten', 'baseten', 'Baseten', 'https://inference.baseten.co/v1', 'chat', ('api_key',), 'api', '/models', 'BASETEN_API_KEY', notes=''),
    ProviderSource('pi', 'cerebras', 'cerebras', 'Cerebras', 'https://api.cerebras.ai/v1', 'chat', ('api_key',), 'api', '/models', 'CEREBRAS_API_KEY', notes=''),
    ProviderSource('pi', 'cloudflare-ai-gateway', 'cloudflare-ai-gateway', 'Cloudflare AI Gateway', 'https://gateway.ai.cloudflare.com/v1/{account_id}/{gateway_id}/compat', 'multi', ('api_key',), 'api', '/models', 'CLOUDFLARE_API_KEY', notes=''),
    ProviderSource('pi', 'cloudflare-workers-ai', 'cloudflare-workers-ai', 'Cloudflare Workers AI', 'https://api.cloudflare.com/client/v4/accounts/{account_id}/ai/v1', 'chat', ('api_key',), 'api', '/models', 'CLOUDFLARE_API_KEY', notes=''),
    ProviderSource('pi', 'deepseek', 'deepseek', 'DeepSeek', 'https://api.deepseek.com/v1', 'chat', ('api_key',), 'api', '/models', 'DEEPSEEK_API_KEY', notes=''),
    ProviderSource('pi', 'fireworks', 'fireworks', 'Fireworks', 'https://api.fireworks.ai/inference/v1', 'multi', ('api_key',), 'api', '/models', 'FIREWORKS_API_KEY', notes=''),
    ProviderSource('pi', 'github-copilot', 'github-copilot', 'GitHub Copilot', 'https://api.individual.githubcopilot.com', 'multi', ('api_key', 'oauth'), 'subscription', '/models', 'COPILOT_GITHUB_TOKEN', notes=''),
    ProviderSource('pi', 'google', 'google', 'Google Gemini API', 'https://generativelanguage.googleapis.com/v1beta', 'gemini', ('api_key',), 'api', '/models', 'GEMINI_API_KEY', notes='Distinct from Hoshimi gemini (Code Assist OAuth).'),
    ProviderSource('pi', 'google-vertex', 'google-vertex', 'Google Vertex AI', 'https://{location}-aiplatform.googleapis.com', 'vertex', ('api_key', 'google_adc'), 'api', 'configured models / publisher catalog', '', notes=''),
    ProviderSource('pi', 'groq', 'groq', 'Groq', 'https://api.groq.com/openai/v1', 'chat', ('api_key',), 'api', '/models', 'GROQ_API_KEY', notes=''),
    ProviderSource('pi', 'huggingface', 'huggingface', 'Hugging Face', 'https://router.huggingface.co/v1', 'chat', ('api_key',), 'api', '/models', 'HF_TOKEN', notes=''),
    ProviderSource('pi', 'kimi-coding', 'kimi-coding', 'Kimi Coding', 'https://api.kimi.com/coding/v1', 'chat', ('api_key', 'oauth'), 'coding', '/models', 'KIMI_API_KEY', notes=''),
    ProviderSource('pi', 'meta', 'meta', 'Meta', 'https://api.meta.ai/v1', 'responses', ('api_key', 'oauth'), 'api', '/models', 'META_API_KEY', notes=''),
    ProviderSource('pi', 'minimax', 'minimax-api', 'MiniMax API', 'https://api.minimax.io/anthropic/v1', 'messages', ('api_key',), 'api', '/models', 'MINIMAX_API_KEY', notes='Existing minimax is the Agent account service; retain its ID.'),
    ProviderSource('pi', 'minimax-cn', 'minimax-cn', 'MiniMax API CN', 'https://api.minimaxi.com/anthropic/v1', 'messages', ('api_key',), 'api', '/models', 'MINIMAX_CN_API_KEY', notes=''),
    ProviderSource('pi', 'mistral', 'mistral', 'Mistral', 'https://api.mistral.ai/v1', 'mistral', ('api_key',), 'api', '/models', 'MISTRAL_API_KEY', notes='Pi calls this mistral-conversations, but sends Chat Completions.'),
    ProviderSource('pi', 'moonshotai', 'moonshotai', 'Moonshot', 'https://api.moonshot.ai/v1', 'chat', ('api_key',), 'api', '/models', 'MOONSHOT_API_KEY', notes=''),
    ProviderSource('pi', 'moonshotai-cn', 'moonshotai-cn', 'Moonshot CN', 'https://api.moonshot.cn/v1', 'chat', ('api_key',), 'api', '/models', 'MOONSHOT_API_KEY', notes=''),
    ProviderSource('pi', 'nvidia', 'nvidia', 'NVIDIA NIM', 'https://integrate.api.nvidia.com/v1', 'chat', ('api_key',), 'api', '/models', 'NVIDIA_API_KEY', notes=''),
    ProviderSource('pi', 'openai', 'openai', 'OpenAI', 'https://api.openai.com/v1', 'responses', ('api_key', 'oauth'), 'api', '/models', 'OPENAI_API_KEY', notes=''),
    ProviderSource('pi', 'openai-codex', 'codex', 'Codex (ChatGPT OAuth)', 'https://chatgpt.com/backend-api/codex', 'responses', ('oauth',), 'subscription', '/models?client_version=0.107.0', '', notes='Reuse existing codex login and account ID.'),
    ProviderSource('pi', 'opencode', 'opencode', 'OpenCode Zen', 'https://opencode.ai/zen/v1', 'multi', ('anonymous', 'api_key'), 'api', '/models', 'OPENCODE_API_KEY', notes=''),
    ProviderSource('pi', 'opencode-go', 'opencode-go', 'OpenCode Go', 'https://opencode.ai/zen/go/v1', 'multi', ('api_key',), 'go', '/models', 'OPENCODE_API_KEY', notes=''),
    ProviderSource('pi', 'openrouter', 'openrouter', 'OpenRouter', 'https://openrouter.ai/api/v1', 'multi', ('api_key', 'oauth'), 'api', '/models/user (fallback /models)', 'OPENROUTER_API_KEY', notes='Existing custom agg accounts continue to work.'),
    ProviderSource('pi', 'qwen-token-plan', 'qwen-token-plan', 'Qwen Token Plan', 'https://token-plan.ap-southeast-1.maas.aliyuncs.com/compatible-mode/v1', 'chat', ('api_key',), 'token-plan', '/models', 'QWEN_TOKEN_PLAN_API_KEY', notes=''),
    ProviderSource('pi', 'qwen-token-plan-cn', 'qwen-token-plan-cn', 'Qwen Token Plan CN', 'https://token-plan.cn-beijing.maas.aliyuncs.com/compatible-mode/v1', 'chat', ('api_key',), 'token-plan-cn', '/models', 'QWEN_TOKEN_PLAN_CN_API_KEY', notes=''),
    ProviderSource('pi', 'qwen-token-plan-individual', 'qwen-token-plan-individual', 'Qwen Token Plan Individual', 'https://token-plan.ap-southeast-1.maas.aliyuncs.com/compatible-mode/v1', 'chat', ('api_key',), 'individual', '/models', 'QWEN_TOKEN_PLAN_API_KEY', notes=''),
    ProviderSource('pi', 'radius', 'radius', 'Radius', 'https://radius.pi.dev', 'pi-messages', ('api_key', 'oauth'), 'api', '/v1/config', 'RADIUS_API_KEY', notes=''),
    ProviderSource('pi', 'together', 'together', 'Together', 'https://api.together.ai/v1', 'chat', ('api_key',), 'api', '/models', 'TOGETHER_API_KEY', notes=''),
    ProviderSource('pi', 'typesafe', 'typesafe', 'TypeSafe', 'https://api.typesafe.ai/v1', 'classifier', ('api_key',), 'api', 'configured classifiers', 'TYPESAFE_API_KEY', notes=''),
    ProviderSource('pi', 'vercel-ai-gateway', 'vercel-ai-gateway', 'Vercel AI Gateway', 'https://ai-gateway.vercel.sh/v1', 'messages', ('api_key',), 'api', '/models', 'AI_GATEWAY_API_KEY', notes=''),
    ProviderSource('pi', 'xai', 'xai', 'xAI', 'https://api.x.ai/v1', 'responses', ('api_key', 'oauth'), 'api', '/models', 'XAI_API_KEY', notes=''),
    ProviderSource('pi', 'xiaomi', 'xiaomi', 'Xiaomi MiMo', 'https://api.xiaomimimo.com/v1', 'chat', ('api_key',), 'api', '/models', 'XIAOMI_API_KEY', notes=''),
    ProviderSource('pi', 'xiaomi-token-plan-ams', 'xiaomi-token-plan-ams', 'Xiaomi Token Plan AMS', 'https://token-plan-ams.xiaomimimo.com/v1', 'chat', ('api_key',), 'ams', '/models', 'XIAOMI_TOKEN_PLAN_AMS_API_KEY', notes=''),
    ProviderSource('pi', 'xiaomi-token-plan-cn', 'xiaomi-token-plan-cn', 'Xiaomi Token Plan CN', 'https://token-plan-cn.xiaomimimo.com/v1', 'chat', ('api_key',), 'cn', '/models', 'XIAOMI_TOKEN_PLAN_CN_API_KEY', notes=''),
    ProviderSource('pi', 'xiaomi-token-plan-sgp', 'xiaomi-token-plan-sgp', 'Xiaomi Token Plan SGP', 'https://token-plan-sgp.xiaomimimo.com/v1', 'chat', ('api_key',), 'sgp', '/models', 'XIAOMI_TOKEN_PLAN_SGP_API_KEY', notes=''),
    ProviderSource('pi', 'zai', 'zai', 'Z.AI Coding', 'https://api.z.ai/api/coding/paas/v4', 'chat', ('api_key',), 'coding', '/models', 'ZAI_API_KEY', notes=''),
    ProviderSource('pi', 'zai-coding-cn', 'zai-coding-cn', 'Z.AI Coding CN', 'https://open.bigmodel.cn/api/coding/paas/v4', 'chat', ('api_key',), 'coding-cn', '/models', 'ZAI_CODING_CN_API_KEY', notes=''),
    ProviderSource('pi-free', 'cline', 'cline', 'Cline', 'https://api.cline.bot/api/v1', 'chat', ('api_key', 'oauth'), 'api', '/models', '', notes=''),
    ProviderSource('pi-free', 'kilo', 'kilo', 'Kilo AI', 'https://api.kilo.ai/api/gateway', 'chat', ('anonymous', 'api_key', 'oauth'), 'api', '/models', '', notes='Anonymous free mode and authenticated catalog/device login.'),
    ProviderSource('pi-free', 'qoder', 'qoder', 'Qoder', 'https://api2-v2.qoder.sh', 'chat', ('pat', 'oauth'), 'community/premium', 'curated', '', notes=''),
    ProviderSource('pi-free', 'zenmux', 'agg', 'ZenMux', 'https://zenmux.ai/api/v1', 'chat', ('api_key',), 'api', '/models', 'ZENMUX_API_KEY', notes=''),
    ProviderSource('pi-free', 'llm7', 'agg', 'LLM7', 'https://api.llm7.io/v1', 'chat', ('api_key',), 'api', '/models', 'LLM7_API_KEY', notes=''),
    ProviderSource('pi-free', 'deepinfra', 'agg', 'DeepInfra', 'https://api.deepinfra.com/v1/openai', 'chat', ('api_key',), 'api', '/models', 'DEEPINFRA_TOKEN', notes=''),
    ProviderSource('pi-free', 'sambanova', 'agg', 'SambaNova', 'https://api.sambanova.ai/v1', 'chat', ('api_key',), 'api', '/models', 'SAMBANOVA_API_KEY', notes=''),
    ProviderSource('pi-free', 'novita', 'agg', 'Novita', 'https://api.novita.ai/openai/v1', 'chat', ('api_key',), 'api', '/models', 'NOVITA_API_KEY', notes=''),
    ProviderSource('pi-free', 'routeway', 'agg', 'Routeway', 'https://api.routeway.ai/v1', 'chat', ('api_key',), 'api', '/models', 'ROUTEWAY_API_KEY', notes=''),
    ProviderSource('pi-free', 'opengateway', 'agg', 'OpenGateway', 'https://opengateway.gitlawb.com/v1', 'chat', ('api_key',), 'api', '/models', 'OPENGATEWAY_API_KEY', notes=''),
    ProviderSource('pi-free', 'tokenrouter', 'agg', 'TokenRouter', 'https://api.tokenrouter.com/v1', 'chat', ('api_key',), 'api', '/models', 'TOKENROUTER_API_KEY', notes=''),
    ProviderSource('pi-free', 'anyapi', 'agg', 'AnyAPI', 'https://api.anyapi.ai/v1', 'chat', ('api_key',), 'api', '/models', 'ANYAPI_API_KEY', notes=''),
    ProviderSource('pi-free', 'bai', 'agg', 'B.AI', 'https://api.b.ai/v1', 'chat', ('api_key',), 'api', '/models', 'BAI_API_KEY', notes=''),
    ProviderSource('pi-free', 'orcarouter', 'agg', 'OrcaRouter', 'https://api.orcarouter.ai/v1', 'chat', ('api_key',), 'api', '/models', 'ORCAROUTER_API_KEY', notes=''),
    ProviderSource('pi-free', 'xkiro', 'agg', 'XKiro', 'https://api.xkiro.com/v1', 'chat', ('api_key',), 'api', '/models', 'XKIRO_API_KEY', notes=''),
    ProviderSource('pi-free', 'fastrouter', 'agg', 'FastRouter', 'https://api.fastrouter.ai/api/v1', 'chat', ('api_key',), 'api', '/models', 'FASTROUTER_API_KEY', notes=''),
    ProviderSource('pi-free', 'requesty', 'agg', 'Requesty', 'https://router.requesty.ai/v1', 'chat', ('api_key',), 'api', '/models', 'REQUESTY_API_KEY', notes=''),
    ProviderSource('pi-free', 'stepfun', 'agg', 'StepFun', 'https://api.stepfun.ai/step_plan/v1', 'chat', ('api_key',), 'api', '/models', 'STEPFUN_API_KEY', notes=''),
    ProviderSource('pi-free', 'gmi', 'agg', 'GMI Cloud', 'https://api.gmi-serving.com/v1', 'chat', ('api_key',), 'api', '/models', 'GMI_API_KEY', notes=''),
    ProviderSource('pi-free', 'agnes', 'agg', 'Agnes AI', 'https://apihub.agnes-ai.com/v1', 'chat', ('api_key',), 'api', '/models', 'AGNES_API_KEY', notes=''),
    ProviderSource('pi-free', 'venice', 'agg', 'Venice AI', 'https://api.venice.ai/api/v1', 'chat', ('api_key',), 'api', '/models', 'VENICE_API_KEY', notes=''),
    ProviderSource('pi-free', 'infron', 'agg', 'Infron', 'https://llm.onerouter.pro/v1', 'chat', ('api_key',), 'api', '/models', 'INFRON_API_KEY', notes=''),
    ProviderSource('pi-free', 'merge', 'agg', 'Merge Gateway', 'https://api-gateway.merge.dev/v1/openai', 'chat', ('api_key',), 'api', 'https://api-gateway.merge.dev/v1/models', 'MERGE_API_KEY', notes=''),
    ProviderSource('pi-free', 'commandcode', 'agg', 'CommandCode', 'https://api.commandcode.ai/provider/v1', 'multi', ('api_key',), 'api', '/models', 'COMMAND_CODE_API_KEY', notes=''),
    ProviderSource('pi-free', 'ollama-cloud', 'agg', 'Ollama Cloud', 'https://ollama.com/v1', 'chat', ('api_key',), 'api', '/models', 'OLLAMA_API_KEY', notes=''),
    ProviderSource("free-claude-code", 'nvidia_nim', 'nvidia', 'NVIDIA NIM', 'https://integrate.api.nvidia.com/v1', 'chat', ('api_key',), 'api', '/models', 'NVIDIA_NIM_API_KEY', notes=''),
    ProviderSource("free-claude-code", 'open_router', 'openrouter', 'OpenRouter', 'https://openrouter.ai/api/v1', 'chat', ('api_key',), 'api', '/models', 'OPENROUTER_API_KEY', notes=''),
    ProviderSource("free-claude-code", 'groq', 'groq', 'Groq', 'https://api.groq.com/openai/v1', 'chat', ('api_key',), 'api', '/models', 'GROQ_API_KEY', notes=''),
    ProviderSource("free-claude-code", 'cline_pass', 'cline-pass', 'ClinePass', 'https://api.cline.bot/api/v1', 'chat', ('api_key',), 'subscription', '/ai/cline/recommended-models (clinePass)', 'CLINE_API_KEY', notes=''),
    ProviderSource("free-claude-code", 'openai', 'codex', 'OpenAI / ChatGPT', 'https://chatgpt.com/backend-api/codex', 'responses', ('oauth',), 'subscription', '/models', '', notes=''),
    ProviderSource("free-claude-code", 'openai_api', 'openai', 'OpenAI API', 'https://api.openai.com/v1', 'responses', ('api_key',), 'api', '/models', 'OPENAI_API_KEY', notes=''),
    ProviderSource("free-claude-code", 'anthropic', 'anthropic', 'Anthropic', 'https://api.anthropic.com/v1', 'messages', ('api_key',), 'api', '/models', 'ANTHROPIC_API_KEY', notes=''),
    ProviderSource("free-claude-code", 'github_copilot', 'github-copilot', 'GitHub Copilot', 'https://api.githubcopilot.com', 'multi', ('oauth',), 'subscription', '/models', '', notes=''),
    ProviderSource("free-claude-code", 'xai', 'xai', 'xAI (Grok)', 'https://api.x.ai/v1', 'chat', ('api_key',), 'api', '/language-models', 'XAI_API_KEY', notes=''),
    ProviderSource("free-claude-code", 'alibaba_cloud', 'alibaba-cloud', 'Alibaba Cloud', 'https://dashscope-intl.aliyuncs.com/compatible-mode/v1', 'chat', ('api_key',), 'api', '/api/v1/models (DashScope paginated)', 'ALIBABA_CLOUD_API_KEY', notes=''),
    ProviderSource("free-claude-code", 'qwencloud', 'qwen-token-plan', 'QwenCloud Token Plan', 'https://token-plan.ap-southeast-1.maas.aliyuncs.com/compatible-mode/v1', 'chat', ('api_key',), 'token-plan', '/models', 'QWENCLOUD_API_KEY', notes=''),
    ProviderSource("free-claude-code", 'qwencloud_coding', 'qwen-coding-plan-intl', 'QwenCloud Coding Plan', 'https://coding-intl.dashscope.aliyuncs.com/v1', 'chat', ('api_key',), 'coding', '/models', 'QWENCLOUD_CODING_API_KEY', notes=''),
    ProviderSource("free-claude-code", 'together', 'together', 'Together AI', 'https://api.together.ai/v1', 'chat', ('api_key',), 'api', '/models', 'TOGETHER_API_KEY', notes=''),
    ProviderSource("free-claude-code", 'deepinfra', 'agg', 'DeepInfra', 'https://api.deepinfra.com/v1/openai', 'chat', ('api_key',), 'api', 'https://api.deepinfra.com/models/list', 'DEEPINFRA_API_KEY', notes=''),
    ProviderSource("free-claude-code", 'siliconflow', 'siliconflow', 'SiliconFlow', 'https://api.siliconflow.com/v1', 'chat', ('api_key',), 'api', '/models?sub_type=chat', 'SILICONFLOW_API_KEY', notes=''),
    ProviderSource("free-claude-code", 'nebius', 'nebius', 'Nebius Token Factory', 'https://api.tokenfactory.nebius.com/v1', 'chat', ('api_key',), 'api', '/models?verbose=true', 'NEBIUS_API_KEY', notes=''),
    ProviderSource("free-claude-code", 'chutes', 'chutes', 'Chutes', 'https://llm.chutes.ai/v1', 'chat', ('api_key',), 'api', '/models', 'CHUTES_API_KEY', notes=''),
    ProviderSource("free-claude-code", 'featherless', 'featherless', 'Featherless AI', 'https://api.featherless.ai/v1', 'chat', ('api_key',), 'api', '/models (paginated, current plan)', 'FEATHERLESS_API_KEY', notes=''),
    ProviderSource("free-claude-code", 'agnes', 'agg', 'Agnes AI', 'https://apihub.agnes-ai.com/v1', 'chat', ('api_key',), 'api', '/models', 'AGNES_API_KEY', notes=''),
    ProviderSource("free-claude-code", 'zenmux', 'agg', 'ZenMux', 'https://zenmux.ai/api/v1', 'chat', ('api_key',), 'api', '/models', 'ZENMUX_API_KEY', notes=''),
    ProviderSource("free-claude-code", 'wandb', 'wandb', 'W&B Inference', 'https://api.inference.wandb.ai/v1', 'chat', ('api_key',), 'api', '/models', 'WANDB_API_KEY', notes=''),
    ProviderSource("free-claude-code", 'azure_openai', 'azure', 'Azure OpenAI', 'https://{resource}.openai.azure.com/openai/v1', 'chat', ('api_key',), 'api', 'configured deployments', 'AZURE_OPENAI_API_KEY', notes=''),
    ProviderSource("free-claude-code", 'gemini', 'google', 'Gemini', 'https://generativelanguage.googleapis.com/v1beta/openai/', 'chat', ('api_key',), 'api', '/models', 'GEMINI_API_KEY', notes='Native Gemini and OpenAI-compatible routes; wire selected per account/model.'),
    ProviderSource("free-claude-code", 'vertex', 'google-vertex', 'Google Vertex AI', 'https://{location}-aiplatform.googleapis.com/v1/projects/{project}/locations/{location}/endpoints/openapi', 'chat', ('google_adc',), 'api', '/v1beta1/publishers/google/models', '', notes='Native Gemini and OpenAI-compatible routes; wire selected per account/model.'),
    ProviderSource("free-claude-code", 'deepseek', 'deepseek', 'DeepSeek', 'https://api.deepseek.com', 'chat', ('api_key',), 'api', '/models', 'DEEPSEEK_API_KEY', notes=''),
    ProviderSource("free-claude-code", 'mistral', 'mistral', 'Mistral', 'https://api.mistral.ai/v1', 'chat', ('api_key',), 'api', '/models', 'MISTRAL_API_KEY', notes=''),
    ProviderSource("free-claude-code", 'mistral_codestral', 'mistral-codestral', 'Mistral Codestral', 'https://codestral.mistral.ai/v1', 'chat', ('api_key',), 'coding', '/models', 'CODESTRAL_API_KEY', notes=''),
    ProviderSource("free-claude-code", 'opencode_zen', 'opencode', 'OpenCode Zen', 'https://opencode.ai/zen/v1', 'multi', ('api_key',), 'api', '/models', 'OPENCODE_API_KEY', notes=''),
    ProviderSource("free-claude-code", 'opencode_go', 'opencode-go', 'OpenCode Go', 'https://opencode.ai/zen/go/v1', 'multi', ('api_key',), 'api', '/models', 'OPENCODE_API_KEY', notes=''),
    ProviderSource("free-claude-code", 'vercel', 'vercel-ai-gateway', 'Vercel AI Gateway', 'https://ai-gateway.vercel.sh/v1', 'chat', ('api_key',), 'api', '/models', 'AI_GATEWAY_API_KEY', notes=''),
    ProviderSource("free-claude-code", 'bedrock', 'bedrock-mantle', 'Amazon Bedrock', 'https://bedrock-mantle.us-east-1.api.aws/v1', 'chat', ('api_key',), 'api', '/models', 'AWS_BEARER_TOKEN_BEDROCK', notes=''),
    ProviderSource("free-claude-code", 'huggingface', 'huggingface', 'Hugging Face', 'https://router.huggingface.co/v1', 'chat', ('api_key',), 'api', '/models', 'HUGGINGFACE_API_KEY', notes=''),
    ProviderSource("free-claude-code", 'cohere', 'cohere', 'Cohere', 'https://api.cohere.ai/compatibility/v1', 'chat', ('api_key',), 'api', '/models', 'COHERE_API_KEY', notes=''),
    ProviderSource("free-claude-code", 'wafer', 'wafer', 'Wafer', 'https://pass.wafer.ai/v1', 'chat', ('api_key',), 'api', '/models', 'WAFER_API_KEY', notes=''),
    ProviderSource("free-claude-code", 'kimi', 'moonshotai', 'Kimi', 'https://api.moonshot.ai/v1', 'chat', ('api_key',), 'api', '/models', 'KIMI_API_KEY', notes=''),
    ProviderSource("free-claude-code", 'kimi_code', 'kimi-coding', 'Kimi Code', 'https://api.kimi.com/coding/v1', 'chat', ('api_key',), 'coding', '/models', 'KIMI_CODE_API_KEY', notes=''),
    ProviderSource("free-claude-code", 'kilo', 'kilo', 'Kilo.ai', 'https://api.kilo.ai/api/gateway', 'chat', ('api_key',), 'api', '/models', 'KILO_API_KEY', notes=''),
    ProviderSource("free-claude-code", 'minimax', 'minimax-chat', 'MiniMax', 'https://api.minimax.io/v1', 'chat', ('api_key',), 'api', '/models', 'MINIMAX_API_KEY', notes=''),
    ProviderSource("free-claude-code", 'cerebras', 'cerebras', 'Cerebras', 'https://api.cerebras.ai/v1', 'chat', ('api_key',), 'api', '/models', 'CEREBRAS_API_KEY', notes=''),
    ProviderSource("free-claude-code", 'sambanova', 'agg', 'SambaNova', 'https://api.sambanova.ai/v1', 'chat', ('api_key',), 'api', '/models', 'SAMBANOVA_API_KEY', notes=''),
    ProviderSource("free-claude-code", 'fireworks', 'fireworks', 'Fireworks', 'https://api.fireworks.ai/inference/v1', 'chat', ('api_key',), 'api', '/models', 'FIREWORKS_API_KEY', notes=''),
    ProviderSource("free-claude-code", 'novita', 'agg', 'Novita AI', 'https://api.novita.ai/openai/v1', 'chat', ('api_key',), 'api', '/models', 'NOVITA_API_KEY', notes=''),
    ProviderSource("free-claude-code", 'cloudflare', 'cloudflare-workers-ai', 'Cloudflare', 'https://api.cloudflare.com/client/v4/accounts/{account_id}/ai/v1', 'chat', ('api_key',), 'api', '/models', 'CLOUDFLARE_API_TOKEN', notes=''),
    ProviderSource("free-claude-code", 'zai', 'zai', 'Z.ai Coding Plan', 'https://api.z.ai/api/coding/paas/v4', 'chat', ('api_key',), 'coding', '/models', 'ZAI_API_KEY', notes=''),
    ProviderSource("free-claude-code", 'zai_api', 'zai-api', 'Z.ai API', 'https://api.z.ai/api/paas/v4', 'chat', ('api_key',), 'api', '/models', 'ZAI_API_KEY', notes=''),
    ProviderSource("free-claude-code", 'tokenrouter', 'agg', 'TokenRouter', 'https://api.tokenrouter.com/v1', 'chat', ('api_key',), 'api', '/models', 'TOKENROUTER_API_KEY', notes=''),
    ProviderSource("free-claude-code", 'nararoute', 'nararoute', 'NaraRoute', 'https://router.bynara.id/v1', 'chat', ('api_key',), 'api', '/models', 'NARAROUTE_API_KEY', notes=''),
    ProviderSource("free-claude-code", 'poolside', 'poolside', 'Poolside AI', 'https://inference.poolside.ai/v1', 'chat', ('api_key',), 'api', '/models', 'POOLSIDE_API_KEY', notes=''),
    ProviderSource("free-claude-code", 'llm7', 'agg', 'LLM7.io', 'https://api.llm7.io/v1', 'chat', ('api_key',), 'api', '/models', 'LLM7_API_KEY', notes=''),
    ProviderSource("free-claude-code", 'scaleway', 'scaleway', 'Scaleway', 'https://api.scaleway.ai/v1', 'chat', ('api_key',), 'api', '/models', 'SCW_SECRET_KEY', notes=''),
    ProviderSource("free-claude-code", 'lightning', 'lightning', 'Lightning AI', 'https://lightning.ai/api/v1', 'chat', ('api_key',), 'api', '/models', 'LIGHTNING_API_KEY', notes=''),
    ProviderSource("free-claude-code", 'experiential', 'experiential', 'Experiential Labs', 'https://api.experientiallabs.ai/v1', 'chat', ('api_key',), 'api', '/models', 'EXPLABS_API_KEY', notes=''),
    ProviderSource("free-claude-code", 'cheaperinference', 'cheaperinference', 'Cheaper Inference', 'https://api.cheaperinference.com/v1', 'chat', ('api_key',), 'api', '/models', 'CHEAPER_INFERENCE_API_KEY', notes=''),
    ProviderSource("free-claude-code", 'orcarouter', 'agg', 'OrcaRouter', 'https://api.orcarouter.ai/v1', 'chat', ('api_key',), 'api', '/models', 'ORCAROUTER_API_KEY', notes=''),
    ProviderSource("free-claude-code", 'atlascloud', 'atlascloud', 'Atlas Cloud', 'https://api.atlascloud.ai/v1', 'chat', ('api_key',), 'api', '/models', 'ATLASCLOUD_API_KEY', notes=''),
    ProviderSource("free-claude-code", 'xkiro', 'agg', 'xKiro', 'https://api.xkiro.com/v1', 'chat', ('api_key',), 'api', '/models', 'XKIRO_API_KEY', notes=''),
    ProviderSource("free-claude-code", 'opper', 'opper', 'Opper', 'https://api.opper.ai/v3/compat', 'chat', ('api_key',), 'api', '/models', 'OPPER_API_KEY', notes=''),
    ProviderSource("free-claude-code", 'ollama_cloud', 'agg', 'Ollama Cloud', 'https://ollama.com/v1', 'chat', ('api_key',), 'api', '/models', 'OLLAMA_API_KEY', notes=''),
    ProviderSource("free-claude-code", 'lmstudio', 'lmstudio', 'LM Studio', 'http://localhost:1234/v1', 'chat', ('none', 'api_key'), 'local', '/models', '', notes=''),
    ProviderSource("free-claude-code", 'llamacpp', 'llamacpp', 'LLaMA.cpp', 'http://localhost:8080/v1', 'chat', ('none', 'api_key'), 'local', '/models', '', notes=''),
    ProviderSource("free-claude-code", 'ollama', 'ollama', 'Ollama', 'http://localhost:11434/v1', 'chat', ('none', 'api_key'), 'local', '/models', '', notes=''),
)
PI_PROVIDERS = tuple(d for d in SOURCE_PROVIDERS if d.source == "pi")
PI_FREE_PROVIDERS = tuple(d for d in SOURCE_PROVIDERS if d.source == "pi-free")
FCC_PROVIDERS = tuple(d for d in SOURCE_PROVIDERS if d.source == "free-claude-code")
