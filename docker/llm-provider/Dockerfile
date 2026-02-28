FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src/llm-provider/ .

ENV PROVIDER_TYPE=openai
ENV CONFIG_PATH=/config/openai.yaml
ENV LOG_DIR=/data

EXPOSE 8000

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
