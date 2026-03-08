from __future__ import annotations

import os

from openai import OpenAI


def main() -> None:
    api_key =  "admin"
    base_url = "http://127.0.0.1:8000/v1"
    client = OpenAI(api_key=api_key, base_url=base_url)

    models = client.models.list()
    model_ids = [item.id for item in models.data]
    # print(f"models: {model_ids}")
    if not model_ids:
        raise RuntimeError("No models available from llm_api_factory")

    response = client.chat.completions.create(
        model="gpt-4",
        messages=[{"role": "user", "content": "ping"}],
        # extra_body={"rules": "qiniu"},
    )
    message = response.choices[0].message.content
    print(f"chat: {message}")


if __name__ == "__main__":
    main()
