# Verifier image. It runs in its own container and sees only the declared artifact, the
# survey tool's source. It holds what the agent never sees: the three runs, the report each
# must produce, and the telemetry archive that serves them. It compiles the collected OCaml
# itself and runs the result against an archive it starts, so every report comes out of the
# agent's own program speaking the archive's protocol.
FROM public.ecr.aws/docker/library/python:3.13-slim-bookworm@sha256:01f42367a0a94ad4bc17111776fd66e3500c1d87c15bbd6055b7371d39c124fb

ENV LANG=C.UTF-8 \
    LC_ALL=C.UTF-8 \
    PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin

# The same OCaml the agent's box carries, and the verifier tooling from a hash-pinned lockfile.
# Nothing is installed at trial time.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ocaml-nox \
    && rm -rf /var/lib/apt/lists/*
COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir --require-hashes --no-deps -r /tmp/requirements.txt && rm -f /tmp/requirements.txt
RUN ocamlopt -version

# Debian installs the OCaml library tree group-writable; only root may write under a
# compiler's library path.
RUN chmod -R go-w /usr/lib/ocaml

RUN useradd --create-home --uid 10001 --shell /bin/bash runner

# Landing directory for the declared artifact.
RUN mkdir -p /app/rover/src /app/rover/bin
RUN chown -R runner:runner /app && chmod 1755 /app

# The runs, the reports and the archive service: root only, never in a container the agent
# reaches.
COPY data /opt/truth
COPY golden /opt/truth/golden
COPY telemetry /opt/archive
RUN chmod 700 /opt/truth /opt/archive

# Separate mode does not upload tests/, so the image owns /tests itself.
COPY . /tests/

WORKDIR /app/rover
