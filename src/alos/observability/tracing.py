"""OpenTelemetry API boundary; SDK/export configuration belongs to deployment."""

from opentelemetry import trace


def get_tracer() -> trace.Tracer:
    return trace.get_tracer("alos-backend")
