# syntax=docker/dockerfile:1
FROM python:3.9.17-slim

RUN apt-get update \
	&& apt-get install -y --no-install-recommends \
		postgresql-client curl build-essential git awscli \
	&& rm -rf /var/lib/apt/lists/*

ENV PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=on \
    PIP_DEFAULT_TIMEOUT=100 \
    POETRY_HOME="/opt/poetry" \
    POETRY_NO_INTERACTION=1 \
    POETRY_VIRTUALENVS_CREATE=false \
    VIRTUAL_ENV="/venv"

# prepend poetry and venv to path
ENV PATH="$POETRY_HOME/bin:$VIRTUAL_ENV/bin:$PATH"

# prepare virtual env
RUN python -m venv $VIRTUAL_ENV

WORKDIR /usr/src/app
ENV PYTHONPATH="/usr/src/app:$PYTHONPATH"

RUN --mount=type=cache,target=/root/.cache \
    curl -sSL https://install.python-poetry.org | python -

COPY poetry.lock pyproject.toml ./
RUN --mount=type=cache,target=/root/.cache \
    poetry install --no-root --only main

COPY . .

COPY --chmod=755 <<'EOF' /usr/src/app/start.sh
#!/bin/bash
set -eux
set -o pipefail

aws ssm get-parameter --output text --query Parameter.Value --with-decryption --name puzzup-env > .env
python manage.py migrate
exec python manage.py runserver 0.0.0.0:8000
EOF

EXPOSE 8000
CMD ["./start.sh"]
