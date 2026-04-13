import requests, time, json

print("Testing Ollama basic response...")
start = time.time()
try:
    r = requests.post(
        "http://localhost:11434/api/generate",
        json={"model": "llama3.1:8b", "prompt": "Say hello in Korean. Reply in one word only.", "stream": False},
        timeout=60,
    )
    elapsed = time.time() - start
    resp = r.json().get("response", "")
    print(f"Time: {elapsed:.1f}s")
    print(f"Response: {resp[:200]}")
except Exception as e:
    print(f"Error: {e}")

print("\nTesting short digest prompt...")
articles = [
    {"title": "AI marketing automation grows 40%", "summary": "New tools emerge for automated ad creation"},
    {"title": "Google updates search with AI overviews", "summary": "GEO becomes critical for marketers"},
    {"title": "OpenAI launches new enterprise tools", "summary": "Enterprise AI adoption accelerates"},
]
prompt = (
    "Classify these 3 news into 3 categories. Write in Korean.\n"
    "Categories: Generative Engine Optimization, AI Automation in Marketing Execution, Marketing AI Trend\n"
    'Return JSON array: [{"title":"category","summary":"2 lines","key_points":["p1","p2"],'
    '"marketing_insight":"1 line","strategic_implication":"1 line"}]\n\n'
    f"News: {json.dumps(articles)}"
)

start = time.time()
try:
    r = requests.post(
        "http://localhost:11434/api/generate",
        json={"model": "llama3.1:8b", "prompt": prompt, "stream": False},
        timeout=120,
    )
    elapsed = time.time() - start
    resp = r.json().get("response", "")
    print(f"Time: {elapsed:.1f}s")
    print(f"Response length: {len(resp)}")
    print(f"Response: {resp[:500]}")
except Exception as e:
    elapsed = time.time() - start
    print(f"Error after {elapsed:.1f}s: {e}")
