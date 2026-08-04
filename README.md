# IBMS Watchdog

IBMS Watchdog is a Windows desktop application for scanning, reviewing, exporting, and safely cleaning up local folders, mapped drives, and NAS shares. It builds a local SQLite index so large directory trees can be searched, filtered, and exported without repeatedly walking the storage device.

## What It Does

- Scans local folders, mapped drives, and UNC/NAS shares.
- Shows results in tree view, files-only view, or folders-only view.
- Filters by Show all, Inactive, Empty, Videos, age threshold, search text, and file extension.
- Supports folder scoping through the left folder panel.
- Supports scan exclusions for folder names, extensions, and minimum file size.
- Shows total size, folder size, current page size, filtered size, and selected size.
- Exports CSV reports for listings, file types, folder summaries, delete audit records, and scan history.
- Moves deleted items to the Windows Recycle Bin.
- Requires a username/password before deletion.
- Supports light/dark theme and Windows notifications.

## Requirements

- Windows 10 or Windows 11
- Python 3.10 or newer
- Read access to folders being scanned
- Delete permission for items that will be moved to the Recycle Bin

NAS behavior depends on the permissions and capabilities of the NAS/file server. A mapped drive such as `Z:\` and a UNC path such as `\\server\share` are both supported.

## Install

Open the project folder in a terminal.

PowerShell:

```powershell
python -m venv venv
.\venv\Scripts\Activate.ps1
python -m pip install --upgrade pip [if required]
python -m pip install -r requirements.txt
```

If PowerShell blocks activation, run this in the same terminal:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
```

Windows Command Prompt:

```bat
python -m venv venv
venv\Scripts\activate.bat
python -m pip install --upgrade pip [if required]
python -m pip install -r requirements.txt
```

## Run

Try to run the application inside the virtual environment (venv).
PowerShell or Command Prompt:

```bat
python main.py
```

Use **Browse** to choose a folder. Configure exclusions before scanning when needed, then use **Rescan** to rebuild the index with those rules.

## Delete Authorization

Before using **Delete Selected**, create at least one authorized delete user.

PowerShell or Command Prompt:

```bat
python tools\setup_auth.py --add-user Admin
```

The script asks for the password twice. Password text is hidden while typing.

List authorized users:

```bat
python tools\setup_auth.py --list-users
```

Remove an authorized user:

```bat
python tools\setup_auth.py --remove-user Admin
```

The app stores authorized delete users here:

```text
data\auth_store.json
```

This file stores usernames, salts, and password hashes. It does **not** store plain-text passwords.

Because `data\auth_store.json` is inside the project, it can be committed to GitHub so every cloned copy uses the same delete authorization users. Anyone with the correct username/password can authorize deletes in the app, but Windows/NAS permissions still decide whether the actual file operation succeeds.

## Important Files

```text
Watchdog\
|-- main.py
|-- requirements.txt
|-- README.md
|-- data\
|   |-- auth_store.json        Shared delete authorization users; can be committed
|   |-- auth_lockout.json      Runtime failed-login lockout state; ignored
|   |-- delete_audit.log       Runtime delete audit log; ignored
|   |-- file_index.db          Runtime scan index; ignored
|   |-- file_index.db-shm      SQLite runtime file; ignored
|   |-- file_index.db-wal      SQLite runtime file; ignored
|   `-- scan_history.json      Runtime scan history; ignored
|-- src\
|   |-- main_window.py         Main UI, filters, selection, export, and deletion flow
|   |-- scanner.py             Background scan thread
|   |-- file_index_tool.py     SQLite schema, indexing, and scan implementation
|   |-- folder_cache.py        In-memory folder hierarchy and size cache
|   |-- models.py              Qt tree and proxy models
|   |-- scan_exclusions.py     Exclusion rule parsing and matching
|   |-- scan_history.py        Scan-history persistence
|   |-- auth.py                Delete authorization and audit logging
|   |-- theme.py               Light and dark QSS themes
|   `-- assets\                Application icons
|-- tools\
|   `-- setup_auth.py          Add/list/remove delete authorization users
`-- tests\                     Automated regression tests
```

## Data and GitHub Behavior

The repository is configured so these generated files are ignored:

```text
data\file_index.db
data\file_index.db-shm
data\file_index.db-wal
data\auth_lockout.json
data\delete_audit.log
data\scan_history.json
```

These files are created while using the app and should stay local to each machine.

This file is not ignored:

```text
data\auth_store.json
```

Commit `data\auth_store.json` only when you want the same delete authorization users to be available after cloning the repository.

Application preferences such as theme and notification settings are stored through Windows settings using:

```text
QSettings("IBMS", "Watchdog")
```

## Filters and Navigation

- Selecting a folder in the folder panel scopes the result area to that folder.
- Display filters limit results to all indexed items, inactive items, empty folders, or videos.
- View mode controls whether results appear as a tree, files only, or folders only.
- The age threshold applies after pressing **Go**.
- File Extensions opens a size/count breakdown; selecting an extension filters the result view.
- Exclusions are scan-time rules. Changing exclusions requires a rescan.
- Page selection and all-pages selection are separate.
- Manual unchecks are retained when navigating between pages.

## CSV Export

The Export CSV dialog supports:

- Current page
- All items matching current filters
- Entire scan
- Selected/checkmarked items
- Current bulk-delete scope
- File type breakdown
- Folder summary
- Delete audit log
- Scan history

CSV files use the Windows list separator when available, so exports open in separate Excel columns on systems that use commas or semicolons.

## Run Tests

```bat
python -m pytest
```

Or with unittest:

```bat
python -m unittest discover -s tests -v
```

Tests use Qt's offscreen platform and do not require the app window to stay visible.

## Troubleshooting

### A NAS path scans slowly

Confirm the share is reachable in Windows Explorer and the current Windows account has permission to enumerate it. NAS performance depends on latency, server load, and the number of directory entries.

### A mapped drive is missing

Mapped drives can differ between elevated and non-elevated Windows sessions. Run Watchdog under the same Windows account and privilege level used to map the drive, or browse to the UNC path directly.

### Delete authorization fails

Check that `data\auth_store.json` exists and contains at least one user:

```bat
python tools\setup_auth.py --list-users
```

If there are too many failed attempts, the temporary lockout state is stored in:

```text
data\auth_lockout.json
```

### Deletion fails after authorization

Watchdog authorization only allows the app to start deletion. The current Windows/NAS user still needs delete permission, and the target storage must support the Recycle Bin operation used by `Send2Trash`.

### Notifications do not appear

Windows system-tray support and notification permissions must be enabled. Status messages inside the application continue to work even when system notifications are unavailable.
