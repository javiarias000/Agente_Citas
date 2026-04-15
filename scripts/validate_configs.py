#!/usr/bin/env python3
"""
Validador de archivos de configuración Arcadium
Verifica JSONs de workflows y configuraciones
"""

import json
import sys
from pathlib import Path
from typing import Dict, Any, List


def validate_json_file(filepath: str) -> Dict[str, Any]:
    """Valida un archivo JSON"""
    result = {
        "path": filepath,
        "exists": False,
        "valid_json": False,
        "structure_ok": False,
        "errors": [],
        "warnings": []
    }

    path = Path(filepath)

    # Existe
    if not path.exists():
        result["errors"].append(f"File does not exist: {filepath}")
        return result

    result["exists"] = True

    # JSON válido
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            data = json.load(f)
        result["valid_json"] = True
    except json.JSONDecodeError as e:
        result["errors"].append(f"Invalid JSON: {e}")
        return result
    except Exception as e:
        result["errors"].append(f"Error reading file: {e}")
        return result

    # Estructura n8n workflow
    if "nodes" in data and isinstance(data["nodes"], list):
        result["structure_ok"] = True

        # Verificar nodos
        node_types = {}
        for i, node in enumerate(data["nodes"]):
            node_type = node.get("type", "unknown")
            node_types[node_type] = node_types.get(node_type, 0) + 1

            # Verificar campos requeridos
            if not node.get("id"):
                result["warnings"].append(f"Node {i} missing 'id'")
            if not node.get("type"):
                result["errors"].append(f"Node {i} missing 'type'")

        result["node_types"] = node_types
        result["total_nodes"] = len(data["nodes"])

        # Buscar webhooks específicos
        webhook_nodes = [
            n for n in data["nodes"]
            if n.get("type") == "n8n-nodes-base.webhook"
        ]
        result["webhook_count"] = len(webhook_nodes)
        result["webhooks"] = [
            n.get("name", "unnamed") for n in webhook_nodes
        ]

    else:
        result["errors"].append("Not a valid n8n workflow (missing 'nodes' array)")
        result["structure_ok"] = False

    return result


def print_result(result: Dict[str, Any]):
    """Imprime resultado de validación"""
    filepath = result["path"]
    print(f"\n📄 {Path(filepath).name}")
    print(f"   Path: {filepath}")

    if not result["exists"]:
        print("   ❌ No existe")
        return

    if not result["valid_json"]:
        print("   ❌ JSON inválido:")
        for err in result["errors"]:
            print(f"     - {err}")
        return

    print(f"   ✅ JSON válido")
    print(f"   📊 Nodos totales: {result.get('total_nodes', 0)}")

    if "node_types" in result:
        print("   🔧 Tipos de nodos:")
        for ntype, count in result.get("node_types", {}).items():
            print(f"     - {ntype}: {count}")

    if "webhooks" in result:
        print("   🌐 Webhooks:")
        for wh in result.get("webhooks", []):
            print(f"     - {wh}")

    if result["warnings"]:
        print("   ⚠️ Warnings:")
        for warn in result["warnings"]:
            print(f"     - {warn}")

    if result["errors"]:
        print("   ❌ Errores:")
        for err in result["errors"]:
            print(f"     - {err}")

    status = "✅ OK" if result["structure_ok"] and not result["errors"] else "❌ FALLÓ"
    print(f"   Status: {status}")


def main():
    """Función principal"""
    print("=" * 60)
    print("VALIDADOR DE CONFIGURACIÓN ARCADIUM")
    print("=" * 60)

    from core.config import settings

    files_to_check = [
        settings.WORKFLOW_JSON_PATH,
        settings.PROCESSING_JSON_PATH
    ]

    all_results = []
    all_ok = True

    for filepath in files_to_check:
        result = validate_json_file(filepath)
        all_results.append(result)
        print_result(result)

        if not result["valid_json"] or not result["structure_ok"] or result["errors"]:
            all_ok = False

    print("\n" + "=" * 60)
    if all_ok:
        print("✅ Todas las configuraciones son válidas")
        sys.exit(0)
    else:
        print("❌ Hay errores en la configuración")
        sys.exit(1)


if __name__ == "__main__":
    main()
