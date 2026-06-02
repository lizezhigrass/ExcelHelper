# Excel Quick Query Helper

Excel Quick Query Helper is a lightweight, background-resident assistant system designed for data-intensive workflows. Standing by in the system tray, it listens for global custom hotkeys (e.g., `Ctrl + Shift + F`) to instantly wake up interactive panels, read columns from the active Excel workbook, perform ultra-fast searches against local or remote database backends, and write selected results back to active Excel rows within milliseconds.

---

## ⚡ Key Features

1. **Hotkey Wake-up & Instant Query**  
   Runs in the notification area (system tray) and wakes up query dialogs or starts auto-fill pipelines via customizable global keyboard hotkeys.
2. **Multi-Sheet Integration & Double-Click Preview**  
   Supports adding multiple file/directory data sources and automatically scans workbooks. In the settings panel, sheets are selected via simple checkboxes. **Double-clicking any sheet** instantly displays a high-fidelity 20-row preview dialog equipped with standard ASCII column letters (`A`, `B`, `C`...) for effortless mapping configuration.
3. **Advanced Three-Column Mappings**  
   The column mapping list is structured in three columns: `[Select, Col Identifier, Field Name]`. The first column displays a centered checkbox. Mappings can be toggled on/off instantly; unchecked mappings are safely discarded on save.
4. **Intelligent Batch Auto-Fill**  
   Processes single rows or multi-selected continuous/non-continuous ranges (using Ctrl-clicks). The background thread processes rows concurrently by performing "read cells -> `limit=1` exact/fuzzy/vector query -> write back," and updates real-time count metrics for "Successful," "Skipped," and "Failed" rows.
5. **Highly Responsive Layouts**  
   Resolves the annoying nested scrolling scrollbar issue by ensuring form tables (Excel column maps, SQL fields, Qdrant payload filters) scale vertically with the window size without nested constraints.
6. **Auto System Locale Sensing & Persistence**  
   Senses the active system language on start (falls back to English in non-Chinese environments) and supports manual overrides ("Auto", "简体中文", "English") in Settings, persisting values directly to `config.yaml`.

---

## 📦 Installation & Environment Guide

### 1. Requirements
- **OS**: Windows 7 / 10 / 11
- **Python**: Python 3.9 or higher

### 2. Virtual Environment Setup & Installation
It is highly recommended to isolate dependencies using Python's built-in `venv`:

```powershell
# 1. Clone or unpack the repository and enter the directory
cd Excel_Helper

# 2. Create a virtual environment
python -m venv .venv

# 3. Activate the environment
.venv\Scripts\activate

# 4. Install all required dependencies
pip install -r requirements.txt
```

> [!TIP]
> To achieve the absolute best reading speed for local Excel files, install the Calamine engine (written in Rust), which reads `.xlsx`/`.xls` files **10x to 30x faster** than pure-Python openpyxl:
> ```powershell
> pip install python-calamine
> ```

### 3. Running the Application
Run the following command while the virtual environment is active to start the resident tray app:
```powershell
python main.py
```
Double-click the lightning tray icon to open the settings console.

---

## ⚙ Config.yaml Specification

All module mappings, interaction controls, and query connections are stored in `config.yaml` under the project root.

### 1. `global` Section
```yaml
global:
  hotkey_enabled: true          # Toggle global hotkey listener
  show_notifications: true      # Toggle balloon notifications in tray
  language: auto                # Display language: auto | zh (Chinese) | en (English)
```

### 2. `query_modules` Section
Defines database query backends. The application supports three types of backends: `excel_file`, `sql_table`, and `qdrant_vector`.
```yaml
query_modules:
  excel_source:                 # Unique module ID
    type: excel_file            # Backend type identifier
    enabled: true               # Enable status
    name: Spare Parts Search    # Friendly alias
    has_header: true            # Whether the sheet contains headers in row 1
    sheet: "Inventory"          # Comma-separated worksheets, e.g., "Inv,History"
    max_rows: 100000            # Max lines to load
    sources:                    # File or folder sources list
      - type: file
        path: H:/data/spare_parts.xlsx
    columns:                    # Column maps (leave empty to load all fields)
      - col: A
        field: PartNo
      - col: B
        field: PartName
```

### 3. `interaction_modules` Section
Manages the user interface when queries are triggered.
```yaml
interaction_modules:
  material_search_ui:
    name: Material Search
    base_type: search_panel     # Interactive template: search_panel | auto_fill | display_panel | popup_table
    components:                 # Inputs and filter controls in the panel
      - field: query_text
        label: Part Name
        comp_type: text
        placeholder: Type part name to search...
      - field: limit_count
        label: Max Rows
        comp_type: number_input
        default: 20
    display_columns:            # Popup table headers and widths
      - field: PartNo
        header: Code
        width: 150
      - field: PartName
        header: Description
        width: 250
```

---

## 🛠 Backend Developer Extension Guide

The system uses an **annotation-based automatic plugin discovery and registration mechanism**. Developers can add custom backend query engines by placing Python files under the plugins folder without modifying any core application logic.

### 1. Three Steps to Extend
1. **Create File**: Add a new Python script under `core/query_backends/` (e.g., `my_custom_query.py`).
2. **Inherit Base Class**: Inherit your class from `QueryBase` (defined in `core.query_base`).
3. **Add Annotation**: Decorate your class with `@register_backend("your_backend_name")`.

### 2. Interface Lifecycle & Core Methods

| Method | Parameters | Return Type | Description |
| :--- | :--- | :--- | :--- |
| `from_config(cls, config)` | `config: dict` | `Self` Instance | Class factory. Used to parse YAML configurations and instantiate the backend. |
| `connect(self)` | None | `bool` (Success status) | Establish connection, warm up model APIs, or read files. Sets `self._status` to `ready`/`error`. |
| `search(self, fields, params)` | `fields: dict`, `params: dict` | `tuple[list[dict], float]` | Core search logic. Accepts query fields (e.g., `{"query": "bearing"}`) and outputs results and elapsed time. |
| `get_status(self)` | None | `str` | Returns the current state, which must be `"ready"`, `"connecting"`, or `"error"`. |
| `get_available_fields(self)` | None | `list[tuple[str, str]]` | Returns a list of available payload fields (key, label) to populate settings dropdowns. |
| `refresh_fields(self)` | None | `list[tuple[str, str]]` | Forces a re-scan of database fields (triggered when clicking the refresh button). |

### 3. Developer Scaffold Template

Here is a standard code template to implement a custom backend:

```python
"""
core/query_backends/my_custom_query.py

Example scaffold for a custom query backend plugin.
"""

from __future__ import annotations
import logging
import time
from typing import Any

# 1. Import abstract bases, registry decorators, and i18n helper
from core.query_base import QueryBase, register_backend
from core.i18n import _t

logger = logging.getLogger(__name__)

# 2. Decorate class with your unique backend type identifier
@register_backend("my_custom_api")
class MyCustomQuery(QueryBase):
    """
    Custom API Query Backend.
    """

    def __init__(self, api_url: str, timeout: int = 10):
        self.api_url = api_url.rstrip("/")
        self.timeout = timeout
        self._status = "connecting"
        
    @classmethod
    def from_config(cls, config: dict[str, Any]) -> "MyCustomQuery":
        """
        Class factory to instantiate from a YAML dictionary config.
        """
        api_url = config.get("api_url", "")
        if not api_url:
            raise ValueError(_t("API 地址不能为空"))
        timeout = int(config.get("timeout", 10))
        return cls(api_url=api_url, timeout=timeout)

    def connect(self) -> bool:
        """
        Connect phase: Perform network handshake or read files here.
        """
        try:
            logger.info("Connecting to custom API at %s...", self.api_url)
            # Simulate latency
            time.sleep(0.5) 
            
            self._status = "ready"
            logger.info("Custom API connected successfully.")
            return True
        except Exception as exc:
            self._status = "error"
            logger.error("Custom API connection failed: %s", exc)
            return False

    def search(
        self,
        fields: dict[str, str],
        params: dict[str, Any],
    ) -> tuple[list[dict], float]:
        """
        Core search logic called in a background worker thread.
        
        fields : Query conditions read from UI or Excel, e.g., {"query": "bearing"}
        params : Search control options (limit, score thresholds, filters)
        """
        query_text = fields.get("query", "").strip()
        if not query_text:
            return [], 0.0

        # Throw translated exceptions in non-Chinese locales
        if self._status != "ready":
            raise RuntimeError(_t("查询后端未就绪（状态: {status}）").format(status=self._status))

        t0 = time.perf_counter()
        
        try:
            # ── Write your custom HTTP request or DB select statement here ──
            # Example:
            # response = requests.post(f"{self.api_url}/search", json={"q": query_text}, timeout=self.timeout)
            # response.raise_for_status()
            # data = response.json()
            
            # Simulated results
            results = [
                {"PartNo": "M1001", "PartName": f"{query_text}_A", "Spec": "100mm", "score": 0.99},
                {"PartNo": "M1002", "PartName": f"{query_text}_B", "Spec": "120mm", "score": 0.85},
            ]
        except Exception as exc:
            elapsed = round(time.perf_counter() - t0, 3)
            logger.error("Custom search failed (took %.3fs): %s", elapsed, exc)
            raise

        elapsed = round(time.perf_counter() - t0, 3)
        return results, elapsed

    def get_status(self) -> str:
        """
        Returns connection state
        """
        return self._status

    def get_available_fields(self) -> list[tuple[str, str]]:
        """
        Returns a list of fields detected on connection: [(key, display_label), ...]
        Used to populate mappings configuration controls in settings.
        """
        return [
            ("PartNo", "PartNo"),
            ("PartName", "PartName"),
            ("Spec", "Specification"),
            ("score", "Search Score"),
        ]

    def refresh_fields(self) -> list[tuple[str, str]]:
        """
        Forces a fresh load of database structures and returns field lists.
        """
        return self.get_available_fields()
```

---

## 📜 License

This project is licensed under the GPLv3 License (prohibiting closed-source commercial exploitation). See the LICENSE file for details.
