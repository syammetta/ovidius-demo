"""Demo script — shows all three interfaces working."""

import asyncio

import httpx


async def demo_qa_endpoint():
    """Demo the FastAPI QA endpoint."""
    print("=" * 60)
    print("DEMO: FastAPI QA Endpoint (POST /qa)")
    print("=" * 60)

    async with httpx.AsyncClient(timeout=60.0) as client:
        resp = await client.post(
            "http://localhost:8000/qa",
            json={"question": "How does tool use work in the Claude API?"},
        )
        data = resp.json()
        print(f"\nAnswer: {data['answer'][:300]}...")
        print(f"\nCitations: {len(data['citations'])} sources")
        for cite in data["citations"]:
            print(f"  [{cite['index']}] {cite['source_title']}")
        print(f"\nLatency: {data['latency_ms']}ms")


async def demo_agent():
    """Demo the managed agent with multi-turn conversation."""
    print("\n" + "=" * 60)
    print("DEMO: Managed Agent (POST /agent/chat)")
    print("=" * 60)

    async with httpx.AsyncClient(timeout=60.0) as client:
        resp = await client.post(
            "http://localhost:8001/agent/chat",
            json={"message": "What is prompt caching and how do I use it?"},
        )
        data = resp.json()
        session_id = data["session_id"]
        print(f"\nSession: {session_id}")
        print(f"Reply: {data['reply'][:300]}...")

        print("\n--- Follow-up ---")
        resp = await client.post(
            "http://localhost:8001/agent/chat",
            json={
                "message": "Does it work with tool use?",
                "session_id": session_id,
            },
        )
        data = resp.json()
        print(f"Reply: {data['reply'][:300]}...")


async def main():
    print("Ovidius Doc QA — Demo\n")
    print("Make sure both servers are running:")
    print("  make serve  (port 8000)")
    print("  make agent  (port 8001)\n")

    await demo_qa_endpoint()
    await demo_agent()

    print("\n" + "=" * 60)
    print("DEMO: MCP Server")
    print("=" * 60)
    print("Run: make mcp")
    print("Then connect via Claude Desktop or any MCP client.")
    print("Tools available: kb_search, kb_answer")


if __name__ == "__main__":
    asyncio.run(main())
