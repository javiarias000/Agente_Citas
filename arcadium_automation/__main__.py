#!/usr/bin/env python3
"""
Punto de entrada para: python -m arcadium_automation
"""

import asyncio
import sys
from pathlib import Path

# Asegurar que el directorio raíz está en el path
sys.path.insert(0, str(Path(__file__).parent.parent))

from main import run_orchestrator


if __name__ == "__main__":
    try:
        asyncio.run(run_orchestrator())
    except KeyboardInterrupt:
        print("\n👋 Shutdown requested by user")
        sys.exit(0)
    except Exception as e:
        print(f"❌ Fatal error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
