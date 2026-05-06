"""Copilot adapter — lightweight CLI bridge to the QA endpoint for non-technical users."""

import argparse
import sys

import httpx


def format_response(data: dict) -> str:
    """Format QA response for terminal display."""
    lines = [data["answer"], ""]

    if data.get("citations"):
        lines.append("Sources:")
        for cite in data["citations"]:
            lines.append(f"  [{cite['index']}] {cite['source_title']}")
            lines.append(f"      {cite['source_url']}")

    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(
        description="Ask questions about documentation from your terminal."
    )
    parser.add_argument("question", nargs="?", help="Your question")
    parser.add_argument(
        "--endpoint", default="http://localhost:8000", help="QA API base URL"
    )
    args = parser.parse_args()

    if args.question:
        question = args.question
    else:
        print("Ask a question (Ctrl+C to exit):")
        try:
            question = input("> ").strip()
        except (KeyboardInterrupt, EOFError):
            sys.exit(0)

    if not question:
        print("No question provided.")
        sys.exit(1)

    try:
        resp = httpx.post(
            f"{args.endpoint}/qa",
            json={"question": question},
            timeout=60.0,
        )
        resp.raise_for_status()
        print(format_response(resp.json()))
    except httpx.ConnectError:
        print(f"Could not connect to {args.endpoint}. Is the API running? (make serve)")
        sys.exit(1)


if __name__ == "__main__":
    main()
