from __future__ import annotations

from typing import Iterable

from openai import OpenAI


def test_endpoint(base_url: str, api_key: str) -> None:
    client = OpenAI(api_key=api_key, base_url=f"{base_url}/v1")

    models = client.models.list()
    model_ids = [item.id for item in models.data]
    print(f"[{base_url}] models: {model_ids}")
    if not model_ids:
        raise RuntimeError(f"No models returned from {base_url}")

    response = client.chat.completions.create(
        model=model_ids[0],
        messages=[{"role": "user", "content": "hello"}],
    )
    message = response.choices[0].message.content
    print(f"[{base_url}] chat: {message}")


def main() -> None:
    targets: Iterable[tuple[str, str]] = (
        ("http://127.0.0.1:9001", "mock-key-1"),
        ("http://127.0.0.1:9002", "mock-key-3"),
    )
    for base_url, api_key in targets:
        test_endpoint(base_url, api_key)


if __name__ == "__main__":
    main()
