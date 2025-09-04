FROM python:3.11-slim

WORKDIR /app

# Copiar requirements primero (para cache de Docker)
COPY requirements.txt .

# Instalar dependencias de Python
RUN pip install --no-cache-dir -r requirements.txt

# Copiar el resto del código
COPY . .

# Puerto que usa Cloud Run
ENV PORT=8080
EXPOSE 8080

# Comando para ejecutar la app
CMD exec uvicorn app:app --host 0.0.0.0 --port ${PORT}