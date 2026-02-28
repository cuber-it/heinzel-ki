FROM python:3.12-slim

WORKDIR /app

RUN pip install --no-cache-dir \
    chainlit httpx \
    pypdf python-docx openpyxl python-pptx \
    pydantic pyyaml

COPY src/llm-provider/file_processor.py .
COPY src/llm-provider/models.py .
COPY src/frontend/chainlit_app.py .

EXPOSE 8000

CMD ["chainlit", "run", "chainlit_app.py", "--host", "0.0.0.0", "--port", "8000"]
