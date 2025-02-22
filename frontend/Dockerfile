FROM python:3.11-slim

WORKDIR /app

# Copy all necessary files
COPY requirements.txt .
COPY app.py .
COPY static/ ./static/
COPY templates/ ./templates/

RUN pip install --no-cache-dir -r requirements.txt

CMD ["gunicorn", "--bind", "0.0.0.0:5000", "app:app"]