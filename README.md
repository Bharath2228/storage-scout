# IBMS Watchdog

IBMS Watchdog is a Windows desktop application for scanning, reviewing, exporting, and safely cleaning up local folders, mapped drives, and NAS shares. It builds a local SQLite index so large directory trees can be searched and filtered without repeatedly walking the storage device.

## Features

- Background scanning for local paths, mapped drives, and UNC network shares.
- Folder browser panel for scoping results to a specific directory.
- Tree, files-only, and folders-only result views.
- Show All, Inactive, Empty, and Videos filters.
- Configurable age threshold with explicit application.
- Scan exclusions for folder names, file extensions, and minimum file size.
- Search and file-extension analysis.
- Paginated results with current-page and all-pages selection.
- Persistent manual selection exclusions while moving between pages.
- Folder, page, filtered-result, and selected-item size summaries.
- CSV exports for listings, file types, folder summaries, delete audit records, and scan history.
- Recoverable deletion through the Windows Recycle Bin.
- Username and password authorization before deletion.
- Light and dark themes.
- Windows notifications after long scans or background completion.

## Requirements

- Windows 10 or Windows 11
- Python 3.10 or newer
- Read access to folders being scanned
- Delete permission for items that will be moved to the Recycle Bin

Network-share behavior depends on the permissions and capabilities of the NAS or file server. A mapped drive such as `Z:\` and a UNC path such as `\\server\share` are both supported.

## Installation

Open PowerShell in the project directory and create a virtual environment:

```powershell
python -m venv venv
.\venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

If PowerShell blocks environment activation, run this once for the current terminal:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
```

## Running the Application

```powershell
python main.py
```

Use **Browse** to choose a directory. Configure exclusions before scanning when necessary, then select **Re-scan** to rebuild the index with those rules.

## Filters and Navigation

The folder panel and result filters work together:

- Selecting a folder scopes the result area to that folder.
- Display filters limit results to inactive items, empty folders, videos, or all indexed items.
- View modes control whether results appear as a tree, files only, or folders only.
- The age threshold applies after pressing **Go**.
- File Extensions opens a size and count breakdown; selecting an extension filters the result view.
- Exclusions are scan-time rules. Changing them requires a re-scan.

When results span multiple pages, page selection and all-pages selection are separate. Manual unchecks are retained when navigating away from a page and returning to it.

## CSV Export

The Export CSV dialog supports several report types and scopes. Available listing scopes include:

- Current page
- All items matching the current filters
- Entire scan
- Selected items
- Current bulk-delete scope

Columns can be enabled individually. CSV files use the Windows list separator when available so exported values open in separate Excel columns on systems that use either commas or semicolons.

## Secured Data

Default security data location:

```text
%APPDATA%\IBMS\Watchdog\
|-- auth_store.json
|-- auth_lockout.json
`-- delete_audit.log
```

This local authorization is intended to prevent casual or accidental deletion on shared workstations. It is not a replacement for Windows, Active Directory, or NAS access controls.

## Runtime Data

The application creates runtime files under `data/`:

```text
data/
|-- file_index.db
|-- file_index.db-shm
|-- file_index.db-wal
`-- scan_history.json
```

These files are generated locally and should not be committed. The database can be rebuilt by running another scan.

Application preferences such as theme and notification settings are stored through `QSettings("IBMS", "Watchdog")`.

## Project Structure

```text
Watchdog/
|-- main.py                    Application entry point
|-- requirements.txt           Runtime Python dependencies
|-- src/
|   |-- main_window.py         Main UI, filters, selection, export, and deletion flow
|   |-- scanner.py             Background scan thread
|   |-- file_index_tool.py     SQLite schema, indexing, and scan implementation
|   |-- folder_cache.py        In-memory folder hierarchy and size cache
|   |-- models.py              Qt tree and proxy models
|   |-- scan_exclusions.py     Exclusion rule parsing and matching
|   |-- scan_history.py        Scan-history persistence
|   |-- auth.py                Delete authorization and audit logging
|   |-- theme.py               Light and dark QSS themes
|   `-- assets/                Application icons
|-- tools/
|   `-- setup_auth.py          Delete-user administration
|-- tests/                     Automated regression tests
`-- data/                      Generated local index and history
```

## Running Tests

The test suite uses Python's standard `unittest` framework:

```powershell
python -m unittest discover -s tests -v
```

Tests use Qt's offscreen platform and do not require the application window to remain visible.

## Troubleshooting

### A NAS path scans slowly

Confirm that the share is reachable in Windows Explorer and that the current Windows account has permission to enumerate it. NAS performance depends on latency, server load, and the number of directory entries.

### A mapped drive is missing

Mapped drives can differ between elevated and non-elevated Windows sessions. Run Watchdog under the same Windows account and privilege level used to map the drive, or browse to the UNC path directly.

### Deletion fails

Verify that an authorized user is configured, the current account has delete permission, and the target storage supports the Windows Recycle Bin operation used by `Send2Trash`.

### Notifications do not appear

Windows system-tray support and notification permissions must be enabled. Status messages inside the application continue to work when system notifications are unavailable.
