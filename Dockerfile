FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY main.py analytics.py auth.py db.py demo_data.py stripe_metrics.py shopify_metrics.py ./
COPY static/ static/

EXPOSE 8420

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8420"]
