from app.api.v1 import routes as routes_module


def test_inspect_stream_chunk_tracks_usage_and_data() -> None:
    buffer, usage_payload, data_seen = routes_module._inspect_stream_chunk(
        "", None, b"data: {\"choices\": []}\n\n"
    )
    assert data_seen is True
    assert usage_payload is None

    buffer, usage_payload, data_seen = routes_module._inspect_stream_chunk(
        buffer,
        usage_payload,
        b"data: {\"usage\": {\"completion_tokens\": 12}}\n\n",
    )
    assert data_seen is True
    assert usage_payload is not None
    assert usage_payload["usage"]["completion_tokens"] == 12

    buffer, usage_payload, data_seen = routes_module._inspect_stream_chunk(
        buffer,
        usage_payload,
        b'data: {"metadata": {"total_usage": {"total_cached_tokens": 4}}}\n\n',
    )
    assert data_seen is True
    assert usage_payload is not None
    assert usage_payload["metadata"]["total_usage"]["total_cached_tokens"] == 4

    buffer, usage_payload, data_seen = routes_module._inspect_stream_chunk(
        buffer, usage_payload, b"data: [DONE]\n\n"
    )
    assert data_seen is False


def test_calculate_tps_handles_missing_values() -> None:
    assert routes_module._calculate_tps(None, 1.0, 10) is None
    assert routes_module._calculate_tps(1.0, 1.0, 10) is None
    assert routes_module._calculate_tps(1.0, 2.0, None) is None


def test_calculate_tps_returns_rate() -> None:
    assert routes_module._calculate_tps(1.0, 3.0, 10) == 5.0
