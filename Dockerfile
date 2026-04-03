# Multi-stage build para optimizar imagen final
FROM python:3.12-slim as builder

WORKDIR /app

# Instalar dependencias del sistema
RUN apt-get update && apt-get install -y \
    gcc \
    g++ \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Copiar requirements e instalar
COPY requirements.txt .
RUN pip install --user --no-cache-dir -r requirements.txt


# Imagen final
FROM python:3.12-slim

WORKDIR /app

# Copiar dependencias instaladas desde builder
COPY --from=builder /root/.local /root/.local
ENV PATH=/root/.local/bin:$PATH

# Copiar código
COPY . .

# Crear usuario no-root para seguridad
RUN useradd -m -u 1000 arcadium && chown -R arcadium:arcadium /app
USER arcadium

# Exponer puertos
EXPOSE 8000 9090

# Comando por defecto
CMD ["uvicorn", "main:create_app", "--host", "0.0.0.0", "--port", "8000"]
