from setuptools import setup, find_packages

setup(
    name="arcadium-automation",
    version="1.0.0",
    author="Arcadium Team",
    description="Sistema de automatización 100% efectivo con landchain",
    packages=find_packages(),
    python_requires=">=3.10",
    install_requires=[
        # Framework Web
        "fastapi>=0.104.0",
        "uvicorn[standard]>=0.24.0",
        "python-multipart>=0.0.6",

        # HTTP
        "aiohttp>=3.9.0",
        "httpx>=0.25.0",
        "requests>=2.31.0",

        # Validación y Configuración
        "pydantic>=2.0.0",
        "pydantic-settings>=2.0.0",
        "jsonschema>=4.19.0",
        "python-dotenv>=1.0.0",

        # Base de Datos
        "sqlalchemy>=2.0.0",
        "alembic>=1.13.0",
        "psycopg2-binary>=2.9.9",
        "psycopg>=3.0.0",  # Para LangGraph PostgreSQLStore
        "pgvector>=0.2.0",  # Opcional: vectores en PostgreSQL

        # LangChain & AI
        "langchain>=0.3.8",
        "langchain-core>=0.3.8",
        "langchain-openai>=0.2.1",
        "langchain-community>=0.0.10",
        "langchain-postgres>=0.0.9",
        "langchain-anthropic>=0.2.1",

        # LangGraph (incluye store: memory + postgres)
        "langgraph>=1.0.0,<2.0.0",
        "langgraph-sdk>=0.1.32",  # Opcional: LangGraph Cloud

        # Utilidades
        "structlog>=24.1.0",
        "tenacity>=8.2.0",
        "psutil>=5.9.0",
        "watchdog>=3.0.0",
        "websockets>=12.0",
        "packaging>=24.0",

        # Monitoreo
        "prometheus-client>=0.19.0",
        "opentelemetry-api>=1.20.0",
        "opentelemetry-sdk>=1.20.0",

        # Cache (opcional)
        "redis>=5.0.0",

        # Google Calendar
        "google-api-python-client>=2.0.0",
        "google-auth-httplib2>=0.1.0",
        "google-auth-oauthlib>=0.4.1",
        "google-auth>=2.0.0",

        # Supabase
        "supabase>=2.3.0",

        # MCP
        "mcp>=0.9.0",
    ],
    extras_require={
        "dev": [
            "pytest>=7.4.0",
            "pytest-asyncio>=0.21.0",
            "pytest-cov>=4.1.0",
            "black>=23.0.0",
            "mypy>=1.5.0",
            "flake8",
        ],
    },
    entry_points={
        "console_scripts": [
            "arcadium=arcadium_automation.cli:main",
        ],
    },
)
