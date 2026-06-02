# Contributing to Excel Search Helper

Thank you for your interest in contributing! This guide explains how to extend the project — especially how to write your own query backend plugin.

---

## Project Architecture

```
┌─────────────────────────────────────────────────────┐
│              Hotkey Trigger Layer                    │
│  (table_modules in config.yaml)                     │
└────────────────────┬────────────────────────────────┘
                     │ triggers
┌────────────────────▼────────────────────────────────┐
│               Mapping Group Layer                   │
│  (mapping_groups) — defines data flow               │
│  - Which Excel columns to read as query input       │
│  - Which result fields to write back                │
└──────────┬─────────────────────┬────────────────────┘
           │ references          │ references
┌──────────▼──────────┐ ┌───────▼────────────────────┐
│  Interaction Module │ │   Query Backend Plugin     │
│  (UI layout)        │ │   (data source)             │
└─────────────────────┘ └────────────────────────────┘
```

The key insight: **the framework is completely business-agnostic**. All behavior is driven by `config.yaml`. Adding a new data source only requires writing a backend plugin.

---

## Writing a Query Backend Plugin

A backend plugin is a single `.py` file placed in `core/query_backends/`. It must:

1. Import and subclass `QueryBase`
2. Decorate the class with `@register_backend("your_type_name")`
3. Implement exactly **4 abstract methods**

### Step 1 — Create the Plugin File

```python
# core/query_backends/my_custom_query.py

"""
My Custom Backend — connects to [your data source].

config.yaml structure:
  type: my_custom_source
  enabled: true
  name: My Custom Data Source
  # ... your custom fields ...
"""

from __future__ import annotations
from typing import Any
import logging

from core.query_base import QueryBase, register_backend

logger = logging.getLogger(__name__)


@register_backend("my_custom_source")  # ← must match 'type:' in config.yaml
class MyCustomQuery(QueryBase):

    def __init__(self, connection_string: str):
        self._connection_string = connection_string
        self._status = "ready"

    @classmethod
    def from_config(cls, config: dict[str, Any]) -> "MyCustomQuery":
        """Parse config.yaml fields and return an instance."""
        connection_string = config.get("connection_string", "")
        return cls(connection_string=connection_string)

    def connect(self) -> bool:
        """
        Establish connection to the data source.
        Return True on success, False on failure.
        Called once when the module is loaded/enabled.
        """
        try:
            # your connection logic here
            logger.info("Connected to custom source: %s", self._connection_string)
            self._status = "ready"
            return True
        except Exception as exc:
            logger.error("Connection failed: %s", exc)
            self._status = "error"
            return False

    def search(
        self,
        fields: dict[str, str],
        params: dict[str, Any],
    ) -> tuple[list[dict], float]:
        """
        Execute a query.

        Parameters
        ----------
        fields : dict
            Query fields populated from Excel cells and UI components.
            Example: {"material_name": "valve", "spec": "DN50"}
        params : dict
            Extra params from query_params in config.yaml.
            Example: {"limit": 20}

        Returns
        -------
        (results, elapsed_seconds)
            results : list of flat dicts, one per result row
                      e.g. [{"material_code": "M001", "material_name": "Valve DN50", ...}]
            elapsed : float, query time in seconds
        """
        import time
        t0 = time.perf_counter()

        limit = int(params.get("limit", 20))
        results = []

        # ── your query logic here ─────────────────────────────
        # Example: filter self._data based on 'fields'
        # for record in self._data:
        #     if all(v.lower() in str(record.get(k, "")).lower()
        #            for k, v in fields.items() if v):
        #         results.append(record)
        #         if len(results) >= limit:
        #             break
        # ─────────────────────────────────────────────────────

        elapsed = time.perf_counter() - t0
        return results, elapsed

    def get_status(self) -> str:
        """Return connection status: 'ready' | 'connecting' | 'error'"""
        return self._status

    def get_available_fields(self) -> list[tuple[str, str]]:
        """
        Return a list of (field_name, display_name) tuples.
        Used by the Settings UI to help users configure column mappings.
        Example: [("material_code", "物料代码"), ("material_name", "物资名称")]
        """
        return [
            ("field_a", "Field A"),
            ("field_b", "Field B"),
        ]
```

### Step 2 — Register in the Frozen Build List

If you also want the plugin to work in PyInstaller packaged `.exe`, add your module to the static list in [`core/query_base.py`](core/query_base.py):

```python
_KNOWN_BACKENDS = [
    "core.query_backends.excel_file_query",
    "core.query_backends.sql_table_query",
    "core.query_backends.qdrant_vector_query",
    "core.query_backends.my_custom_query",   # ← add your module here
]
```

And add it to the `hiddenimports` in [`main.spec`](main.spec).

### Step 3 — Configure in `config.yaml`

```yaml
query_modules:
  my_source_id:
    type: my_custom_source          # ← matches @register_backend("...")
    enabled: true
    name: My Custom Data Source
    connection_string: "..."        # your custom config fields
```

That's it! The Settings UI will automatically detect your new backend type and allow users to configure it graphically.

---

## Built-in Backends Reference

| Type Key | File | Description |
| :--- | :--- | :--- |
| `excel_file` | `excel_file_query.py` | Local Excel files (calamine/openpyxl) |
| `sql_table` | `sql_table_query.py` | SQLite / PostgreSQL generic table query |
| `qdrant_vector` | `qdrant_vector_query.py` | Qdrant vector semantic search |
| `vector_api` | `vector_query.py` | Generic REST API vector search |

---

## Development Setup

```bash
# 1. Clone the repository
git clone https://github.com/your-username/excel-search-helper.git
cd excel-search-helper

# 2. Create a virtual environment
python -m venv .venv
.venv\Scripts\activate

# 3. Install core dependencies
pip install -r requirements-core.txt

# 4. (Optional) Install vector search dependencies
pip install -r requirements-vector.txt

# 5. Generate test data
python test/generate_test_data.py

# 6. Copy and customize config
copy config.example.yaml config.yaml

# 7. Run the application
python main.py
```

---

## Code Style Guidelines

- Use **type annotations** on all function signatures
- Add a **module-level docstring** explaining the file's purpose and `config.yaml` structure
- Use `logger = logging.getLogger(__name__)` — do not use bare `print()` for diagnostics
- Inherit from `QueryBase` and use `@register_backend("type_name")` — do not modify the core framework

---

## Submitting a Pull Request

1. Fork the repository and create a feature branch: `git checkout -b feature/my-backend`
2. Write your backend plugin in `core/query_backends/`
3. Update `_KNOWN_BACKENDS` in `core/query_base.py`
4. Add a usage example to `config.example.yaml`
5. Open a Pull Request with a description of your data source and use case

We welcome backends for: **MySQL, MongoDB, CSV files, HTTP APIs, Redis, and more!**
