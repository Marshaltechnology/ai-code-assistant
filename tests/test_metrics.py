import re

import pytest
from flask import Flask

from app.observability import init_observability, record_usage
from app.observability.metrics import (
    MAX_SERIES_PER_METRIC,
    REGISTRY,
    Counter,
    Histogram,
)

SAMPLE_LINE = re.compile(r"^[a-zA-Z_:][a-zA-Z0-9_:]*(\{[^}]*\})? -?\d+(\.\d+)?([eE][+-]?\d+)?$")
TOKEN = "test-metrics-token"


def make_app(**config):
    app = Flask(__name__)
    app.config["TESTING"] = True
    app.config.setdefault("METRICS_TOKEN", TOKEN)
    app.config.setdefault("METRICS_ENABLED", True)
    app.config.update(config)
    init_observability(app)

    @app.get("/items/<int:item_id>")
    def item(item_id):
        return {"id": item_id}

    return app


def metrics_get(client):
    """Read /metrics with the bearer token used by the test app."""
    return client.get("/metrics", headers={"Authorization": f"Bearer {TOKEN}"})


@pytest.fixture()
def client():
    REGISTRY.reset()
    yield make_app().test_client()
    REGISTRY.reset()


def test_counter_renders_help_type_and_sample():
    counter = Counter("demo_total", "A demo.", ("kind",))
    counter.inc(2, kind="a")
    lines = counter.render()
    assert lines[0] == "# HELP demo_total A demo."
    assert lines[1] == "# TYPE demo_total counter"
    assert 'demo_total{kind="a"} 2' in lines


def test_counter_escapes_label_values():
    counter = Counter("esc_total", "Escaping.", ("kind",))
    counter.inc(kind='a"b\\c\nd')
    assert 'esc_total{kind="a\\"b\\\\c\\nd"} 1' in counter.render()


def test_counter_rejects_negative_and_wrong_labels():
    counter = Counter("neg_total", "Negative.", ("kind",))
    with pytest.raises(ValueError):
        counter.inc(-1, kind="a")
    with pytest.raises(ValueError):
        counter.inc(1, other="a")


def test_counter_cardinality_is_bounded():
    counter = Counter("cap_total", "Cap.", ("kind",))
    for i in range(MAX_SERIES_PER_METRIC + 50):
        counter.inc(kind=f"k{i}")
    samples = [line for line in counter.render() if not line.startswith("#")]
    assert len(samples) == MAX_SERIES_PER_METRIC + 1
    assert 'cap_total{kind="other"} 50' in samples


def test_histogram_renders_cumulative_buckets_sum_and_count():
    histogram = Histogram("lat_seconds", "Latency.", ("kind",), (0.1, 0.5, 1.0))
    histogram.observe(0.05, kind="a")
    histogram.observe(0.75, kind="a")
    lines = histogram.render()
    assert lines[0] == "# HELP lat_seconds Latency."
    assert lines[1] == "# TYPE lat_seconds histogram"
    assert 'lat_seconds_bucket{kind="a",le="0.1"} 1' in lines
    assert 'lat_seconds_bucket{kind="a",le="0.5"} 1' in lines
    assert 'lat_seconds_bucket{kind="a",le="1"} 2' in lines
    assert 'lat_seconds_bucket{kind="a",le="+Inf"} 2' in lines
    assert 'lat_seconds_sum{kind="a"} 0.8' in lines
    assert 'lat_seconds_count{kind="a"} 2' in lines


def test_histogram_cardinality_is_bounded():
    histogram = Histogram("cap_lat_seconds", "Cap.", ("kind",), (1.0,))
    for i in range(MAX_SERIES_PER_METRIC + 5):
        histogram.observe(0.5, kind=f"k{i}")
    samples = [line for line in histogram.render() if "_count{" in line]
    assert len(samples) == MAX_SERIES_PER_METRIC + 1
    assert 'cap_lat_seconds_count{kind="other"} 5' in samples


def test_metrics_endpoint_returns_valid_exposition(client):
    client.get("/items/1")
    response = metrics_get(client)
    assert response.status_code == 200
    assert response.content_type.startswith("text/plain")
    assert "version=0.0.4" in response.content_type
    body = response.get_data(as_text=True)
    assert body.endswith("\n")
    for line in body.strip().splitlines():
        assert line.startswith("#") or SAMPLE_LINE.match(line), line


def test_metrics_declares_all_families(client):
    body = metrics_get(client).get_data(as_text=True)
    for name in ("http_requests_total", "llm_tokens_total", "llm_cost_usd_total"):
        assert f"# TYPE {name} counter" in body
    assert "# TYPE llm_latency_seconds histogram" in body


def test_request_counter_uses_route_pattern_not_raw_path(client):
    client.get("/items/1")
    client.get("/items/2")
    body = metrics_get(client).get_data(as_text=True)
    assert (
        'http_requests_total{method="GET",endpoint="/items/<int:item_id>",' 'status="2xx"} 2'
    ) in body
    assert "/items/1" not in body


def test_unknown_paths_share_one_series(client):
    client.get("/nope-1")
    client.get("/nope-2")
    body = metrics_get(client).get_data(as_text=True)
    assert ('http_requests_total{method="GET",endpoint="unmatched",status="4xx"} 2') in body


def test_token_cost_and_latency_metrics_are_published(client):
    record_usage("openai", "gpt-x", 10, 5, 0.25, latency_ms=250)
    record_usage("openai", "gpt-x", 2, 1, 0.5, latency_ms=750)
    body = metrics_get(client).get_data(as_text=True)
    assert 'llm_tokens_total{provider="openai",model="gpt-x",direction="in"} 12' in body
    assert 'llm_tokens_total{provider="openai",model="gpt-x",direction="out"} 6' in body
    assert 'llm_cost_usd_total{provider="openai",model="gpt-x"} 0.75' in body
    assert 'llm_latency_seconds_count{provider="openai",model="gpt-x"} 2' in body
    assert 'llm_latency_seconds_sum{provider="openai",model="gpt-x"} 1' in body


def test_record_usage_ignores_missing_values(client):
    record_usage("openai", "gpt-x", None, None, None)
    body = metrics_get(client).get_data(as_text=True)
    assert 'provider="openai"' not in body


def test_metrics_endpoint_can_be_disabled():
    REGISTRY.reset()
    response = metrics_get(make_app(METRICS_ENABLED=False).test_client())
    assert response.status_code == 404


def test_metrics_requires_the_configured_bearer_token(client):
    missing = client.get("/metrics")
    assert missing.status_code == 401
    assert missing.headers["WWW-Authenticate"] == 'Bearer realm="metrics"'
    assert "authentication required" in missing.get_data(as_text=True)

    wrong = client.get("/metrics", headers={"Authorization": "Bearer wrong"})
    assert wrong.status_code == 401
    assert wrong.headers["Cache-Control"] == "no-store"

    malformed = client.get("/metrics", headers={"Authorization": "Basic abc"})
    assert malformed.status_code == 401

    allowed = client.get("/metrics", headers={"Authorization": f"Bearer {TOKEN}"})
    assert allowed.status_code == 200


def test_metrics_fails_closed_without_a_token_or_admin_session():
    REGISTRY.reset()
    response = make_app(METRICS_TOKEN="").test_client().get("/metrics")
    assert response.status_code == 401
    assert "WWW-Authenticate" not in response.headers
    REGISTRY.reset()
