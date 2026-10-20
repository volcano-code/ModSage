FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY pyproject.toml ./
COPY requirements.lock ./
COPY modsage ./modsage
RUN pip install --no-cache-dir --require-hashes -r requirements.lock && pip install --no-cache-dir --no-deps . && useradd --uid 10001 --create-home appuser && mkdir -p /app/data && chown appuser:appuser /app/data
USER appuser
EXPOSE 8000
CMD ["uvicorn", "modsage.main:app", "--host", "0.0.0.0", "--port", "8000"]
