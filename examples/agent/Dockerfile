FROM python:3.12-slim

ENV PIP_NO_CACHE_DIR=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    SETUPTOOLS_SCM_PRETEND_VERSION=0.0.0

WORKDIR /app

COPY pyproject.toml README.md LICENSE ./
COPY feishu ./feishu
RUN python -m pip install --upgrade pip \
    && python -m pip install ".[gateway,openai,ws]"

COPY examples/agent/app.py ./app.py

CMD ["python", "app.py"]
