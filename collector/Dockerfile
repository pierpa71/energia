FROM python:3.13-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY collector.py app.py export_xlsx.py backup.py ./
COPY static ./static
EXPOSE 8090
CMD ["python", "-u", "app.py"]
