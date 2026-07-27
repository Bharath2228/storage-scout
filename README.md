# 📁 IBMS Watchdog — Exchange Drive Scanner

A professional desktop application designed for **scanning, auditing, and safely managing** shared network drives, NAS, and local storage. It helps IT administrators and users identify stale data and reclaim storage space with confidence.

Built with **Python 3.10+** and **PyQt6**, featuring a modern, responsive interface.

---

## ✨ Key Features

| Feature | Description |
| :--- | :--- |
| **🔍 Background Scanning** | High-performance recursive scanning that runs in a background thread to keep the UI responsive. |
| **🚥 Smart Classification** | Automatically categorizes folders as **Active**, **Inactive**, or **Empty** based on usage. |
| **🗓️ Flexible Filtering** | Filter results by status, specific date ranges, or a sliding "Age Threshold" (3–24 months). |
| **🛡️ Safe Deletion** | Integrated with `send2trash` — all deletions move to the **Recycle Bin** for easy recovery. |
| **📂 Explorer Integration** | Double-click any item or use the action button to open its location in Windows Explorer instantly. |
| **📊 Audit Reports** | Export your filtered view to a **CSV report** for documentation or further analysis. |
| **🎨 Premium UI** | A clean, professional light-themed interface with color-coded status badges and smooth interaction. |

---

## 🛠️ Project Structure

```text
Watchdog/
├── main.py              # Application entry point
├── requirements.txt     # Python dependencies
├── README.md            # Documentation
└── src/
    ├── scanner.py       # Multi-threaded scanning logic & classification engine
    ├── models.py        # Custom TreeModel for high-performance data handling
    ├── main_window.py   # Primary UI components and user interactions
    ├── theme.py         # Professional QSS styling engine
    └── assets/          # SVG iconography and visual resources
```

---

## 🚀 Getting Started

### Prerequisites
- **Windows OS** (optimized for Explorer integration)
- **Python 3.10+**

### Installation

1. **Clone the repository**
   ```powershell
   git clone https://github.com/IBMS-Offenburg/Watchdog.git
   cd Watchdog
   ```

2. **Setup Virtual Environment**
   ```powershell
   python -m venv venv
   .\venv\Scripts\Activate.ps1
   ```

3. **Install Dependencies**
   ```powershell
   pip install -r requirements.txt
   ```

4. **Run Application**
   ```powershell
   python main.py
   ```

---

## 🧠 Classification Logic

The "Watchdog" uses a sophisticated approach to identify stale data:

- **🟢 Active**: Folders or files modified within the user-defined **Age Threshold**.
- **🟠 Inactive**: Items that haven't been touched since the threshold date.
- **🔴 Empty**: Folders that contain no files, even if they have sub-folders (which are also checked).
- **🔵 Pending**: Items currently being analyzed by the background scanner.

---

## 🛡️ Safety First

- **No Permanent Deletion**: The "Delete Selected" action never bypasses the Recycle Bin.
- **Double Confirmation**: Users must confirm any bulk deletion through a native system dialog.
- **Delete Authorization**: Deletion requires a pre-configured username and password every time.
- **Scan Interruption**: You can stop a running scan at any time without losing the data already collected.

### Delete authorization setup

Authorized delete users are managed outside the running app:

```powershell
python tools/setup_auth.py --add-user jsmith
python tools/setup_auth.py --remove-user jsmith
python tools/setup_auth.py --list-users
```

Passwords are stored as salted PBKDF2 hashes in `%APPDATA%\IBMS\Watchdog\auth_store.json`, never plaintext. Delete authorization and completion events are appended to `%APPDATA%\IBMS\Watchdog\delete_audit.log`. Existing files beside the application are copied there once when needed.

This local authorization is deterrence-grade protection for shared workstations. It is not a substitute for enterprise identity controls. On Windows, the app restricts the credential, lockout, and audit files to the current Windows account.

---

## 📈 Development & Contributions

The application is modularized for easy maintenance:
- To modify **UI layout**, look into `main_window.py`.
- To adjust **Visual Styles**, edit `theme.py`.
- To update **Scanning Rules**, see `scanner.py`.

---
*Created for IBMS Offenburg — Reclaiming storage, one folder at a time.*
