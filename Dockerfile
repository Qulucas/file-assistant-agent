FROM python:3.13-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY falcon_agent ./falcon_agent
COPY agent.py server.py .env.example ./
COPY static ./static
COPY workspace ./workspace

EXPOSE 8000

CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8000"]
