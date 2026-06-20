import httpx
from pathlib import Path

env = {}
for line in Path(".env").read_text().splitlines():
    line = line.strip()
    if line and not line.startswith("#") and "=" in line:
        k, _, v = line.partition("=")
        env[k.strip()] = v.strip()

key = env.get("NETRA_LLM__API_KEY", "")
base = env.get("NETRA_LLM__BASE_URL", "https://api.openai.com/v1").rstrip("/")
verify = r"C:\Users\sunis\.certs\uv-ca-bundle.pem"
headers = {"Authorization": f"Bearer {key}"}

print(f"Endpoint : {base}")
print(f"Key      : {key[:12]}...")
print()

# List all models
r = httpx.get(f"{base}/models", headers=headers, verify=verify)
print(f"GET /models -> {r.status_code}")
if r.status_code == 200:
    data = r.json().get("data", [])
    print(f"  {len(data)} model(s) available:")
    for m in data:
        print(f"  - {m['id']}")
else:
    print("  Error:", r.text[:200])

print()

# Try Qwen 3 235B model IDs commonly used by aggregators
candidates = [
    "qwen3-235b-a22b",
    "Qwen/Qwen3-235B-A22B",
    "qwen-3-235b-instruct",
    "Qwen3-235B-A22B-Instruct",
]
print("Probing Qwen 3 235B candidate IDs...")
for model_id in candidates:
    payload = {
        "model": model_id,
        "messages": [{"role": "user", "content": "hi"}],
        "max_tokens": 5,
    }
    resp = httpx.post(f"{base}/chat/completions", headers=headers, json=payload, verify=verify, timeout=10)
    tag = "OK" if resp.status_code == 200 else f"{resp.status_code}"
    snippet = ""
    if resp.status_code != 200:
        snippet = " | " + resp.text[:100]
    print(f"  [{tag}] {model_id}{snippet}")
