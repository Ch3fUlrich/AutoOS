import json

d = json.load(open('catalog/ai-registry.json', encoding='utf-8'))
prov = d['providers']
models = d['models']

legs = [
    "groq/openai/gpt-oss-120b", "groq/openai/gpt-oss-20b", "groq/qwen/qwen3.8-27b",
    "huggingface/zai-org/GLM-5.2", "huggingface/deepseek-ai/DeepSeek-V4-Flash-0731",
    "huggingface/Qwen/Qwen3.8-27B",
    "openrouter/qwen/qwen3.8-27b:free", "openrouter/nvidia/nemotron-3-super-120b-a12b:free",
    "openrouter/cohere/north-mini-code:free", "openrouter/poolside/laguna-s-2.1:free",
    "cheaperinference/glm-5.2", "cheaperinference/kimi-k3", "cheaperinference/deepseek-v4-flash",
    "vertex/gemini-3.8-flash",
]
for leg in legs:
    prefix, _, mid = leg.partition('/')
    pid = prefix if prefix in prov else next(
        (k for k, v in prov.items() if isinstance(v, dict) and v.get('omniroute_id') == prefix), None)
    p = prov.get(pid) or {}
    print("%-58s provider=%-18s known=%-5s model_row=%-5s tier=%s" % (
        leg, pid, bool(p), mid in models, (models.get(mid) or {}).get('tier', p.get('tier'))))
