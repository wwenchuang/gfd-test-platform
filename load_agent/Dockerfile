ARG K6_IMAGE=grafana/k6:0.52.0
ARG PYTHON_IMAGE=python:3.12.5-slim-bookworm
FROM ${K6_IMAGE} AS k6

FROM ${PYTHON_IMAGE}
COPY --from=k6 /usr/bin/k6 /usr/local/bin/k6
WORKDIR /app
COPY load_agent /app/load_agent
RUN useradd --system --uid 10001 --home /var/lib/midscene-load-agent loadagent \
    && mkdir -p /var/lib/midscene-load-agent \
    && chown -R loadagent:loadagent /var/lib/midscene-load-agent /app
USER 10001
ENV PYTHONUNBUFFERED=1 \
    AGENT_DATA_DIR=/var/lib/midscene-load-agent
CMD ["python", "-m", "load_agent.main"]
