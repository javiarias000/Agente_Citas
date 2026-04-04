# 📦 Instrucciones de Instalación - Arcadium Automation

## ⚠️ Requisitos del Sistema

**Python 3.9 o superior** (se recomienda 3.12)

Verifica tu versión:

```bash
python3 --version
```

Si no tienes Python 3.9+, instala:

```bash
sudo apt update
sudo apt install -y python3 python3-pip python3-venv
```

---

## 🚀 Instalación en 3 Pasos

### **Paso 1: Navegar al directorio**

```bash
cd /home/jav/arcadium_automation
```

### **Paso 2: Instalar dependencias**

**Opción A: Con entorno virtual (RECOMENDADO)**

```bash
# Crear entorno virtual
python3 -m venv venv

# Activar (Linux/Mac)
source venv/bin/activate

# Activar (Windows PowerShell)
# venv\Scripts\Activate.ps1

# Instalar dependencias
pip install --upgrade pip
pip install -r requirements.txt
```

**Opción B: Usando el instalador automático**

```bash
bash install_simple.sh
```

**Opción C: Instalación global (NO recomendado)**

```bash
pip3 install --user -r requirements.txt
```

### **Paso 3: Configurar variables de entorno**

```bash
# Copiar archivo de ejemplo
cp .env.example .env

# Editar con tu editor favorito
nano .env  # o vim, code, etc.
```

Configura **al menos** estas variables:

```bash
N8N_BASE_URL=http://localhost:5678
# Si usas audio:
OPENAI_API_KEY=sk-tu-clave-aqui
```

---

## ✅ Verificar Instalación

```bash
# Si usaste entorno virtual, actívalo primero:
source venv/bin/activate

# Validar configuración
python validate_configs.py

# Probar importaciones
python -c "from arcadium_automation import settings; print('OK')"

# Ejecutar demo
python quickstart.py
```

---

## 🏃 Ejecutar el Sistema

### **Modo Desarrollo (con logs en consola)**

```bash
# Activar venv (si no está activado)
source venv/bin/activate

# Ejecutar
python -m arcadium_automation start
```

### **Modo Producción (con run.sh)**

```bash
# Activar venv primero
source venv/bin/activate

# Usar run.sh (recomendado)
./run.sh start

# En background
nohup ./run.sh start > /dev/null 2>&1 &
```

---

## 🐛 Solución de Problemas

### **Error: "No module named 'pydantic'"**

```bash
# Reactiva el entorno virtual
source venv/bin/activate
# Reinstala
pip install -r requirements.txt
```

### **Error: "python3-venv: command not found"**

```bash
# Instalar venv (requiere sudo)
sudo apt update
sudo apt install -y python3.12-venv
```

### **Error: "Address already in use" (puerto 9090)**

Cambia el puerto en `.env`:

```bash
METRICS_PORT=9091
```

### **Error: Importaciones fallan**

```bash
# Verifica que el venv esté activo (debe ver (venv) en el prompt)
which python  # Debe apuntar a .../arcadium_automation/venv/bin/python

# Si no, activa:
source venv/bin/activate
```

---

## 📁 Estructura esperada después de instalación

```
/home/jav/arcadium_automation/
├── venv/                    # Entorno virtual (creado por ti)
├── core/
├── utils/
├── chains/
├── validators/
├── tests/
├── logs/                   # Creado automáticamente
├── data/                   # Creado automáticamente
├── .env                    # Creado por ti
├── requirements.txt
├── install_simple.sh
├── run.sh
└── quickstart.py
```

---

## 🔄 Comandos Útiles

```bash
# Reactivar entorno virtual (cada nueva terminal)
source venv/bin/activate

# Ver estado del sistema
./run.sh status

# Ver métricas
./run.sh metrics

# Ver logs
./run.sh logs

# Consola interactiva
./run.sh shell

# Ejecutar tests
./run.sh test

# Procesar webhook manual
./run.sh process --file test_payload.json
```

---

## 📊 Test de Funcionamiento

```bash
# 1. Activar venv
source venv/bin/activate

# 2. Ejecutar todos los tests
pytest tests/ -v

# 3. Verificar sintaxis de todos los módulos
python -m py_compile core/*.py chains/*.py utils/*.py

# 4. Demo completo
python quickstart.py
```

---

## ⏭️ Siguientes Pasos después de Instalar

1. ✅ Configurar `.env` con tus API keys
2. ✅ Probar `./run.sh demo`
3. ✅ Ejecutar `./run.sh validate`
4. ✅ Iniciar n8n en otro terminal: `docker-compose up -d n8n` (si usas Docker)
5. ✅ Ejecutar `./run.sh start`
6. ✅ Configurar webhook en n8n para que apunte a tu Arcadium

---

## 🆘 Soporte

1. Revisa logs: `./run.sh logs`
2. Valida config: `./run.sh validate`
3. Ejecuta tests: `./run.sh test`
4. Consulta README.md: Ver documentación completa
5. Guía rápida: Ver COMPLETE_GUIDE.md

---

**🎉 Una vez instalado, el sistema es 100% efectivo con Landchain Architecture!**
