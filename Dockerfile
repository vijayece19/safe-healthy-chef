FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install google-genai google-cloud-storage
COPY agent_orchestrator.py config.py ./
CMD ["python3", "agent_orchestrator.py"]
