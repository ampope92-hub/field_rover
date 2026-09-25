# The rover tooling box. The run itself is not in this image: the frames live in the
# telemetry archive's own image and are only reachable through its socket. Nothing from
# tests/ or solution/ is copied here.
FROM public.ecr.aws/docker/library/python:3.13-slim-bookworm@sha256:01f42367a0a94ad4bc17111776fd66e3500c1d87c15bbd6055b7371d39c124fb

ENV LANG=C.UTF-8 \
    LC_ALL=C.UTF-8

# ocaml-nox is the compiler, the standard library and the unix library; tmux and asciinema
# are what the agent runtime needs.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        asciinema \
        ca-certificates \
        ocaml-nox \
        tmux \
    && rm -rf /var/lib/apt/lists/*
RUN ocamlopt -version

# Where the archive listens here. Other deployments set these to something else.
ENV TELEMETRY_HOST=127.0.0.1 \
    TELEMETRY_PORT=7700

WORKDIR /app/rover
COPY rover/ /app/rover/
RUN chmod +x /app/rover/build.sh /app/rover/run.sh
