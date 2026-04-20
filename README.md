# NAS Watchdog

A desktop application for scanning, auditing, and safely cleaning up stale or empty folders on shared network drives (NAS / Exchange drives / local drives).

Built with **Python + PyQt6**

---

## Features

| Feature | Description |
|---|---|
| **Deep Folder Scanning** | Recursively scans any local or network path in a background thread — the **Re-scan** button toggles to **Stop** to cancel mid-way |
| **Status Classification** | Every folder and file is classified as **Active**, **Inactive**, or **Empty** based on last-modified timestamps |
| **Smart Filters** | Filter by status (All / Active / Inactive / Empty), date range, or "older than N months" |
| **Checkbox Selection** | Select individual files or tick a parent folder to auto-select everything inside it |
| **Safe Deletion** | All deletions go to the Windows Recycle Bin via `send2trash` — nothing is permanently deleted immediately |
| **Open in Explorer** | Single-click the Action column or double-click any row to open the location in Windows Explorer. Files are highlighted automatically |
| **Right-Click Menu** | Context menu on any row: Open Location, Copy Path, Delete |
| **Live Scan Progress** | Status bar shows the exact path being scanned in real time |
| **Export CSV** | Export a full report of all currently visible items to a `.csv` file for auditing |
| **Modern Dark UI** | Premium dark theme with color-coded status badges |

---

## Getting Started

### Prerequisites

- Python 3.10 or higher
- Windows (uses `os.startfile` and `explorer /select` for file browsing)

### 1. Clone the repository

```bash
git clone https://github.com/IBMS-Offenburg/nas-watchdog.git
cd nas-watchdog
```

### 2. Create and activate a virtual environment

```powershell
python -m venv venv
.\venv\Scripts\Activate.ps1
```

### 3. Install dependencies

```bash
pip install -r requirements.txt
```

### 4. Run the application

```bash
python main.py
```

---

## Dependencies

```
PyQt6
send2trash
qdarktheme
```

All listed in [`requirements.txt`](requirements.txt).

---

## Project Structure

```
nas-watchdog/
├── main.py              # Entry point — initializes theme and launches the window
├── requirements.txt     # Python dependencies
├── .gitignore
├── README.md
└── src/
    ├── __init__.py
    ├── scanner.py       # Background QThread — recursive folder scanning and classification
    ├── models.py        # PyQt6 data models (TreeModel and FilterProxyModel)
    └── main_window.py   # Main UI window, filter panel, delegates, and all user actions
```

---

## How Filters Work

Open the filter panel by clicking **Filters** in the top bar.

### Display Mode

Select which items to show:

- **All folders & files** — shows everything (default)
- **Active only** — items modified recently, within the stale threshold
- **Inactive only** — items not modified within the stale threshold
- **Empty only** — folders that contain no files at all

### Date Range

Show only items whose last modified date falls between a From and To date.
Both fields default to **Any** (no filter). Use **Clear dates** to reset.

### Stale Age Threshold

The slider (3 to 24 months) defines what counts as "Inactive".

- Slider at **3 months** (minimum) — no age filter applied
- Slider at **12 months** — any file or folder not touched in 12 months is marked Inactive

> **Note:** The threshold is applied when you click **Re-scan**, as it affects how the scanner classifies files. The date range filter is applied instantly without re-scanning.

---

## Safety

- Deletions always go to the Windows Recycle Bin — files can be restored at any time
- A confirmation dialog is shown before any deletion is performed
- Scanning runs in a background thread — the application remains responsive during long scans

---

## Development

To make changes, activate the virtual environment and run the application:

```powershell
.\venv\Scripts\Activate.ps1
python main.py
```

The source code is split into three focused modules:

- `scanner.py` — add new classification logic here
- `models.py` — change filtering and sorting behaviour here
- `main_window.py` — change the UI layout and interactions here