import re
from urllib.parse import quote, unquote

GEMINI_MODEL_PATH_PATTERN = re.compile(
    r"(?P<prefix>/(?:v1|v1beta|v1alpha)/(?P<collection>models|tunedModels)/)"
    r"(?P<model>[^:?#]+)"
    r"(?P<suffix>:[^/?#]+)?"
)


def extract_gemini_model_alias(path: str) -> str | None:
    match = GEMINI_MODEL_PATH_PATTERN.search(path)
    if not match:
        return None
    model = unquote(match.group("model")).strip("/")
    if not model:
        return None
    return model


def rewrite_gemini_model_path(path: str, real_model: str) -> str:
    match = GEMINI_MODEL_PATH_PATTERN.search(path)
    if not match:
        return path

    collection = match.group("collection")
    replacement = str(real_model or "").strip()
    if replacement.startswith(f"{collection}/"):
        replacement = replacement[len(collection) + 1 :]
    if not replacement:
        return path

    encoded_model = quote(replacement, safe="/")
    return (
        f"{path[:match.start('model')]}"
        f"{encoded_model}"
        f"{path[match.end('model'):]}"
    )
