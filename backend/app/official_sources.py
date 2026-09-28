"""Fixed official patrol list for the 24h digest. Direct HTTP/RSS only."""

import re

# (name, kind, url)  kind: rss | github_releases | homepage | hf_daily
OFFICIAL_SOURCES: list[tuple[str, str, str]] = [
    ("OpenAI News", "rss", "https://openai.com/news/rss.xml"),
    ("OpenAI Changelog", "homepage", "https://platform.openai.com/docs/changelog"),
    ("Anthropic News", "homepage", "https://www.anthropic.com/news"),
    ("Claude Docs Changelog", "homepage", "https://docs.claude.com/en/release-notes/overview"),
    ("Google AI Blog", "rss", "https://blog.google/technology/ai/rss/"),
    ("DeepMind Blog", "homepage", "https://deepmind.google/discover/blog/"),
    ("Meta AI", "rss", "https://ai.meta.com/blog/rss/"),
    ("xAI News", "homepage", "https://x.ai/news"),
    ("Microsoft Azure OpenAI", "homepage", "https://learn.microsoft.com/azure/ai-foundry/openai/whats-new"),
    ("DeepSeek", "homepage", "https://www.deepseek.com/"),
    ("DeepSeek GitHub", "github_releases", "https://github.com/deepseek-ai/DeepSeek-V3/releases.atom"),
    ("Qwen Blog", "rss", "https://qwenlm.github.io/blog/index.xml"),
    ("Qwen GitHub", "github_releases", "https://github.com/QwenLM/Qwen3/releases.atom"),
    ("Kimi / Moonshot", "homepage", "https://www.moonshot.cn/"),
    ("智谱 GLM", "homepage", "https://www.zhipuai.cn/"),
    ("MiniMax", "homepage", "https://www.minimax.io/"),
    ("ByteDance Seed", "homepage", "https://seed.bytedance.com/"),
    ("腾讯混元", "homepage", "https://hunyuan.tencent.com/"),
    ("百度文心", "homepage", "https://yiyan.baidu.com/"),
    ("NVIDIA AI Blog", "rss", "https://blogs.nvidia.com/blog/category/deep-learning/feed/"),
    ("AMD AI", "homepage", "https://www.amd.com/en/newsroom.html"),
    ("Hugging Face Blog", "rss", "https://huggingface.co/blog/feed.xml"),
    ("Hugging Face Daily Papers", "hf_daily", ""),
    ("Stability AI", "homepage", "https://stability.ai/news"),
    ("Runway", "homepage", "https://runwayml.com/blog"),
    ("Luma AI", "homepage", "https://lumalabs.ai/news"),
    ("MCP Spec", "github_releases", "https://github.com/modelcontextprotocol/modelcontextprotocol/releases.atom"),
    ("OpenAI Agents SDK", "github_releases", "https://github.com/openai/openai-agents-python/releases.atom"),
    ("Claude Agent SDK", "github_releases", "https://github.com/anthropics/claude-agent-sdk-python/releases.atom"),
    ("LangGraph", "github_releases", "https://github.com/langchain-ai/langgraph/releases.atom"),
    ("AutoGen", "github_releases", "https://github.com/microsoft/autogen/releases.atom"),
    ("Qwen-Agent", "github_releases", "https://github.com/QwenLM/Qwen-Agent/releases.atom"),
]

# Community / trade-press news. rss_broad is a general portal: only model-release headlines are kept.
NEWS_FEEDS: list[tuple[str, str, str]] = [
    ("新智元", "wordpress", "https://aiera.com.cn/wp-json/wp/v2/posts?per_page=12&_fields=id,date,link,title,excerpt"),
    ("量子位", "rss", "https://www.qbitai.com/feed"),
    ("雷锋网", "rss", "https://www.leiphone.com/feed"),
    ("The Decoder", "rss", "https://the-decoder.com/feed/"),
    ("MarkTechPost", "rss", "https://www.marktechpost.com/feed/"),
    ("Ars Technica AI", "rss", "https://arstechnica.com/ai/feed/"),
    ("MIT Technology Review AI", "rss", "https://www.technologyreview.com/topic/artificial-intelligence/feed/"),
    ("TechCrunch AI", "rss", "https://techcrunch.com/category/artificial-intelligence/feed/"),
    ("The Verge AI", "rss", "https://www.theverge.com/rss/ai-artificial-intelligence/index.xml"),
    ("Linux.do 最新", "rss", "https://linux.do/latest.rss"),
    ("IT之家", "rss_broad", "https://www.ithome.com/rss/"),
    ("36氪", "rss_broad", "https://www.36kr.com/feed"),
    ("爱范儿", "rss_broad", "https://www.ifanr.com/feed"),
    ("极客公园", "rss_broad", "https://www.geekpark.net/rss"),
    ("Solidot", "rss_broad", "https://www.solidot.org/index.rss"),
    ("开源中国", "rss_broad", "https://www.oschina.net/news/rss"),
    ("InfoQ 中文", "rss_broad", "https://www.infoq.cn/feed"),
    ("GitHub Changelog", "rss_broad", "https://github.blog/changelog/feed/"),
    ("Simon Willison", "rss_broad", "https://simonwillison.net/atom/everything/"),
]

_AI_HEADLINE = re.compile(
    r"大模型|开源模型|多模态|基座模型|基础模型|"
    r"\bGPT(?:[-\s]?\d)?|\bClaude\b|\bGemini\b|\bGrok\b|\bxAI\b|"
    r"DeepSeek|深度求索|Qwen|通义|千问|\bKimi\b|月之暗面|智谱|\bGLM\b|"
    r"文心|混元|MiniMax|稀宇|阶跃|StepFun|\bMiMo\b|豆包|"
    r"\bLlama\b|Mistral|\bSora\b|Anthropic|OpenAI|"
    r"智能体|模型权重|开源权重",
    re.I,
)


def headline_is_ai(title: str, summary: str = "") -> bool:
    text = f"{title}\n{summary}"
    if _AI_HEADLINE.search(text):
        return True
    return "模型" in text and any(word in text for word in ("发布", "开源", "上线", "更新"))


# Phase-2 bilingual queries. Snippets are clues only; callers must fetch pages.
FRONTIER_SEARCHES: list[tuple[str, str]] = [
    ("web", "LLM large language model official release"),
    ("web", "大模型 正式发布 官网"),
    ("web", "multimodal image video generation model release"),
    ("web", "视频生成 图像生成 模型 发布"),
    ("web", "AI agent harness framework release"),
    ("web", "智能体 Agent 框架 更新"),
    ("web", "NVIDIA AMD GPU AI chip announcement"),
    ("web", "site:linux.do 大模型 发布"),
    ("web", "site:jiqizhixin.com 发布"),
]

AGENTIC_SEARCHES: list[tuple[str, str]] = [
    ("github", "stars:>300 agent framework tool use"),
    ("github", "stars:>200 SWE-agent OpenHands Aider MemGPT LangGraph"),
    ("s2", "ReAct language agents reasoning acting Yao"),
    ("s2", "generative agents memory baseline Park"),
    ("arxiv", "language agent survey harness memory ReAct"),
    ("web", "established LLM agent frameworks github OpenHands SWE-agent"),
]

# Seed works that already have independent use or are treated as baselines.
AGENTIC_CLASSICS: list[dict[str, str]] = [
    {
        "title": "ReAct: Synergizing Reasoning and Acting in Language Models",
        "source": "arxiv",
        "external_id": "2210.03629",
        "source_url": "https://arxiv.org/abs/2210.03629",
        "published_at": "2022-10-06",
        "summary": "交错推理与行动的工具调用范式，后续 Agent 工作的常见基线。",
    },
    {
        "title": "Reflexion: Language Agents with Verbal Reinforcement Learning",
        "source": "arxiv",
        "external_id": "2303.11366",
        "source_url": "https://arxiv.org/abs/2303.11366",
        "published_at": "2023-03-20",
        "summary": "用语言反思做言语强化学习，被大量后继工作引用。",
    },
    {
        "title": "Generative Agents: Interactive Simulacra of Human Behavior",
        "source": "arxiv",
        "external_id": "2304.03442",
        "source_url": "https://arxiv.org/abs/2304.03442",
        "published_at": "2023-04-07",
        "summary": "记忆流与反思规划的社会模拟 Agent，记忆机制经典来源。",
    },
    {
        "title": "MemGPT: Towards LLMs as Operating Systems",
        "source": "arxiv",
        "external_id": "2310.08560",
        "source_url": "https://arxiv.org/abs/2310.08560",
        "published_at": "2023-10-12",
        "summary": "把上下文当内存层级管理，工程里常见的记忆方案。",
    },
    {
        "title": "Voyager: An Open-Ended Embodied Agent with Large Language Models",
        "source": "arxiv",
        "external_id": "2305.16291",
        "source_url": "https://arxiv.org/abs/2305.16291",
        "published_at": "2023-05-25",
        "summary": "开放世界技能库与课程式探索，自进化 Agent 的代表实现。",
    },
    {
        "title": "SWE-agent: Agent-Computer Interfaces Enable Automated Software Engineering",
        "source": "arxiv",
        "external_id": "2405.15793",
        "source_url": "https://arxiv.org/abs/2405.15793",
        "published_at": "2024-05-06",
        "summary": "为软件工程 Agent 设计 ACI，SWE-bench 上被广泛复用。",
    },
    {
        "title": "SWE-bench: Can Language Models Resolve Real-World GitHub Issues?",
        "source": "arxiv",
        "external_id": "2310.06770",
        "source_url": "https://arxiv.org/abs/2310.06770",
        "published_at": "2023-10-10",
        "summary": "真实 GitHub issue 评测，后续软件 Agent 的标准基准。",
    },
    {
        "title": "All-Hands-AI/OpenHands",
        "source": "github",
        "external_id": "github:All-Hands-AI/OpenHands",
        "source_url": "https://github.com/All-Hands-AI/OpenHands",
        "published_at": "2024-03-13",
        "summary": "开源软件工程 Agent 平台，有持续维护和广泛使用。",
    },
    {
        "title": "Aider-AI/aider",
        "source": "github",
        "external_id": "github:Aider-AI/aider",
        "source_url": "https://github.com/Aider-AI/aider",
        "published_at": "2023-05-01",
        "summary": "终端结对编程 Agent，实际工程使用面很广。",
    },
    {
        "title": "langchain-ai/langgraph",
        "source": "github",
        "external_id": "github:langchain-ai/langgraph",
        "source_url": "https://github.com/langchain-ai/langgraph",
        "published_at": "2024-01-17",
        "summary": "有状态 Agent 图编排库，生产里常见的 harness。",
    },
    {
        "title": "microsoft/autogen",
        "source": "github",
        "external_id": "github:microsoft/autogen",
        "source_url": "https://github.com/microsoft/autogen",
        "published_at": "2023-08-22",
        "summary": "多 Agent 对话框架，后续工作常拿来对照。",
    },
    {
        "title": "Toolformer: Language Models Can Teach Themselves to Use Tools",
        "source": "arxiv",
        "external_id": "2302.04761",
        "source_url": "https://arxiv.org/abs/2302.04761",
        "published_at": "2023-02-09",
        "summary": "自监督学会调用工具的早期关键论文。",
    },
]

# Open search (phase 2) stays on enabled sources + this query.
AGENTIC_ARXIV = (
    'all:"language agent" OR all:"agentic" OR all:"tool use" OR all:"ReAct" '
    'OR all:harness OR all:"process supervision" OR all:"generative agents" '
    'OR all:"MemGPT" OR all:"SWE-agent"'
)
