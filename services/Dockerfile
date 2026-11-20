FROM python:3.12-slim
WORKDIR /app
RUN pip install --no-cache-dir 'aiohttp>=3.9,<4'
COPY markcontrol/ ./markcontrol/
COPY services/diagnostics_receiver.py ./services/diagnostics_receiver.py
ENV PYTHONUNBUFFERED=1 DIAGNOSTICS_HOST=0.0.0.0 PYTHONPATH=/app
USER 10001:10001
CMD ["python", "services/diagnostics_receiver.py"]
