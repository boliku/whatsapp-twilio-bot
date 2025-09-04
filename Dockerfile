FROM python:3.11-slim

WORKDIR /app

# Instalar dependencias del sistema si es necesario
RUN apt-update && apt-get install -y \
    && rm -rf /var/lib/apt/lists/*

# Copiar requirements
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copiar código
COPY . .

# Puerto que usa Cloud Run
ENV PORT=8080
EXPOSE 8080

# Comando para ejecutar la app
CMD exec uvicorn app:app --host 0.0.0.0 --port ${PORT}