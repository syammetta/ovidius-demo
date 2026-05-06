"""Copilot adapter — terminal CLI for non-technical users to query the IRS knowledge base.

Supports single-shot and interactive multi-turn modes. Designed to be the simplest
possible entry point: no API keys needed on the client, just a running QA server.

Usage:
    python -m app.copilot.adapter "What is the standard deduction?"
    python -m app.copilot.adapter --interactive
    python -m app.copilot.adapter --endpoint https://deployed.example.com
"""

import argparse
import sys

import httpx

BLUE = "\033[34m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
CYAN = "\033[36m"
DIM = "\033[2m"
BOLD = "\033[1m"
RESET = "\033[0m"


def format_response(data: dict, use_color: bool = True) -> str:
    if not use_color:
        return _format_plain(data)

    lines = []

    confidence = data.get("confidence", "")
    if confidence == "LOW_CONFIDENCE":
        lines.append(f"{YELLOW}[Low confidence — the knowledge base may not fully cover this topic]{RESET}")
        lines.append("")

    lines.append(f"{BOLD}{data['answer']}{RESET}")
    lines.append("")

    if data.get("citations"):
        lines.append(f"{DIM}Sources:{RESET}")
        for cite in data["citations"]:
            lines.append(f"  {CYAN}[{cite['index']}]{RESET} {cite['source_title']}")
            lines.append(f"      {DIM}{cite['source_url']}{RESET}")

    total_ms = data.get("total_ms")
    if total_ms:
        lines.append("")
        lines.append(f"{DIM}{total_ms:.0f}ms total | {data.get('retrieval_method', 'hybrid')}{RESET}")

    return "\n".join(lines)


def _format_plain(data: dict) -> str:
    lines = [data["answer"], ""]
    if data.get("citations"):
        lines.append("Sources:")
        for cite in data["citations"]:
            lines.append(f"  [{cite['index']}] {cite['source_title']}")
            lines.append(f"      {cite['source_url']}")
    return "\n".join(lines)


def ask(endpoint: str, question: str, session_id: str | None = None) -> dict:
    payload = {"question": question}
    resp = httpx.post(f"{endpoint}/qa", json=payload, timeout=60.0)
    resp.raise_for_status()
    return resp.json()


def interactive_mode(endpoint: str, use_color: bool = True):
    c = BLUE if use_color else ""
    r = RESET if use_color else ""
    d = DIM if use_color else ""

    print(f"{c}Ovidius Tax QA{r} — ask questions about IRS tax documentation")
    print(f"{d}Type 'quit' or Ctrl+C to exit{r}")
    print()

    while True:
        try:
            question = input(f"{GREEN}>{RESET} " if use_color else "> ").strip()
        except (KeyboardInterrupt, EOFError):
            print()
            break

        if not question:
            continue
        if question.lower() in ("quit", "exit", "q"):
            break

        try:
            data = ask(endpoint, question)
            print()
            print(format_response(data, use_color=use_color))
            print()
        except httpx.ConnectError:
            print(f"Could not connect to {endpoint}. Is the API running? (make serve)")
        except httpx.HTTPStatusError as e:
            print(f"API error: {e.response.status_code}")


def main():
    parser = argparse.ArgumentParser(
        description="Ask questions about IRS tax documentation from your terminal."
    )
    parser.add_argument("question", nargs="?", help="Your question (omit for interactive mode)")
    parser.add_argument(
        "--endpoint", default="http://localhost:8000", help="QA API base URL"
    )
    parser.add_argument(
        "--interactive", "-i", action="store_true", help="Interactive multi-turn mode"
    )
    parser.add_argument(
        "--no-color", action="store_true", help="Disable colored output"
    )
    args = parser.parse_args()

    use_color = not args.no_color and sys.stdout.isatty()

    if args.interactive or (not args.question and sys.stdin.isatty()):
        interactive_mode(args.endpoint, use_color=use_color)
        return

    if args.question:
        question = args.question
    else:
        question = sys.stdin.read().strip()

    if not question:
        print("No question provided. Use --interactive for multi-turn mode.")
        sys.exit(1)

    try:
        data = ask(args.endpoint, question)
        print(format_response(data, use_color=use_color))
    except httpx.ConnectError:
        print(f"Could not connect to {args.endpoint}. Is the API running? (make serve)")
        sys.exit(1)


if __name__ == "__main__":
    main()
