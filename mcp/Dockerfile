FROM python:3.11-slim

WORKDIR /app

RUN pip install --no-cache-dir uv

COPY pyproject.toml uv.lock ./
COPY notion_mcp/ notion_mcp/

RUN uv pip install --system .

EXPOSE 8000

CMD ["uv", "run", "notion-api"]
