from setuptools import setup, find_packages

setup(
    name="arcadium-automation",
    version="1.0.0",
    author="Arcadium Team",
    description="Sistema de automatización 100% efectivo con landchain",
    packages=find_packages(),
    python_requires=">=3.9",
    install_requires=[
        "aiohttp>=3.9.0",
        "pydantic>=2.0.0",
        "pydantic-settings>=2.0.0",
        "jsonschema>=4.19.0",
        "python-dotenv>=1.0.0",
        "tenacity>=8.2.0",
        "structlog>=24.1.0",
        "psutil>=5.9.0",
        "watchdog>=3.0.0",
        "requests>=2.31.0",
        "websockets>=12.0",
        "prometheus-client>=0.19.0",
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
        "redis": ["redis>=5.0.0"],
        "db": ["sqlalchemy>=2.0.0", "alembic>=1.13.0"],
    },
    entry_points={
        "console_scripts": [
            "arcadium=arcadium_automation.cli:main",
        ],
    },
)
