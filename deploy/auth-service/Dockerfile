FROM python:3.12-slim

WORKDIR /app

RUN pip install --no-cache-dir \
    fastapi \
    uvicorn \
    python-multipart \
    "passlib[bcrypt]" \
    "bcrypt<4.1"

COPY auth_service.py .

EXPOSE 5000

CMD ["uvicorn", "auth_service:app", "--host", "0.0.0.0", "--port", "5000"]
