FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app.py .
ENV PORT=8090
EXPOSE 8090
# Small prod server; single worker is plenty for a read-only viewer.
RUN pip install --no-cache-dir gunicorn>=21
CMD ["gunicorn", "-b", "0.0.0.0:8090", "-w", "2", "--timeout", "30", "app:app"]
