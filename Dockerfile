# Enterprise image — Playwright Chromium + headless deps pre-installed (hybrid mode is real here)
FROM mcr.microsoft.com/playwright/python:v1.44.0-jammy AS base
WORKDIR /app
COPY requirements.txt pyproject.toml ./
RUN pip install --no-cache-dir -r requirements.txt \
 && python -m playwright install --with-deps chromium
COPY src/ ./src/
COPY config/ ./config/
COPY frontend/ ./frontend/
COPY cli.py ./
ENV PYTHONPATH=/app/src PATHFINDER_CFG=/app/config/enterprise.yaml
EXPOSE 8099
CMD ["uvicorn", "redirect_pathfinder.api:app", "--host", "0.0.0.0", "--port", "8099"]
