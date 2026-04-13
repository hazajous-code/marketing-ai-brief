from newsletter_builder import _fetch_live_ai_tools
tools = _fetch_live_ai_tools(6)
print(f"Fetched {len(tools)} AI tools")
for t in tools[:3]:
    title = t.get("title", "")[:60]
    print(f"  - {title}")
