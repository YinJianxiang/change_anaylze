"""Minimal, safe connectivity check for the configured Responses API."""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

from orchestrator.mail_ingest import load_env_file


def responses_url(configured: str) -> str:
    value = configured.rstrip("/")
    return value if value.endswith("/responses") else value + "/responses"


def check_connection(env_file: Path, timeout: int = 60) -> int:
    load_env_file(env_file)
    api_key = os.environ.get("OPENAI_API_KEY", "").strip()
    model = os.environ.get("OPENAI_MODEL", "gpt-5").strip()
    configured_url = os.environ.get("OPENAI_RESPONSES_URL", "https://api.openai.com/v1").strip()
    if not api_key:
        print("ERROR: OPENAI_API_KEY is missing")
        return 2
    if not configured_url:
        print("ERROR: OPENAI_RESPONSES_URL is missing")
        return 2

    url = responses_url(configured_url)
    payload = json.dumps({"model": model, "input": "Reply with exactly: pong"}).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=payload,
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        method="POST",
    )
    print(f"Checking {url}")
    print(f"Model: {model}")
    print(f"API key: present ({len(api_key)} characters)")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read().decode("utf-8", errors="replace")
            print(f"SUCCESS: HTTP {response.status}")
            try:
                value = json.loads(body)
                output_text = value.get("output_text", "")
                if not output_text:
                    output_text = next(
                        (content.get("text", "") for item in value.get("output", [])
                         for content in item.get("content", []) if content.get("type") == "output_text"),
                        "",
                    )
                print(f"Response ID: {value.get('id', '')}")
                print(f"Status: {value.get('status', '')}")
                print(f"Output: {output_text}")
            except json.JSONDecodeError:
                print(body[:1000])
            return 0
    except urllib.error.HTTPError as error:
        body = error.read().decode("utf-8", errors="replace")
        print(f"ERROR: HTTP {error.code}")
        print(body[:1000])
        return 1
    except urllib.error.URLError as error:
        print(f"ERROR: connection failed: {error.reason}")
        return 1
    except TimeoutError:
        print(f"ERROR: request timed out after {timeout} seconds")
        return 1


def main() -> int:
    parser = argparse.ArgumentParser(description="Test the configured OpenAI-compatible Responses API.")
    parser.add_argument("--env-file", type=Path, default=Path(__file__).resolve().parent / ".env")
    parser.add_argument("--timeout", type=int, default=60)
    args = parser.parse_args()
    if args.timeout < 1:
        parser.error("--timeout must be greater than zero")
    return check_connection(args.env_file, args.timeout)


if __name__ == "__main__":
    sys.exit(main())
