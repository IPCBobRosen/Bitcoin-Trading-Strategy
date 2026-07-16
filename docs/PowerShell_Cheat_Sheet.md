# PowerShell Cheat Sheet

This document contains the PowerShell commands used throughout the BTS (Bitcoin Trading System) project.

---

# Navigation

## Show current directory

```powershell
pwd
```

Displays the current working directory.

---

## List files and folders

```powershell
dir
```

Lists the files and folders in the current directory.

---

## Change into a folder

```powershell
cd app
```

Moves into the `app` folder.

---

## Go back one folder

```powershell
cd ..
```

Moves up one directory.

---

# File Operations

## Move a file

```powershell
mv main.py app\
```

Moves `main.py` into the `app` folder.

---

## Copy a file

```powershell
cp app\main.py app\main_backup.py
```

Creates a copy of `main.py`.

---

## Delete a file

```powershell
rm app\main_backup.py
```

Deletes the file.

---

## Create a folder

```powershell
mkdir app
```

Creates a new folder.

---

## Create multiple folders

```powershell
mkdir app,tests,docs,config,logs,scripts,data
```

Creates several folders at once.


# Special Path Symbols

| Symbol | Meaning | Example |
|--------|---------|---------|
| `.` | Current directory | `git add .` |
| `..` | Parent directory | `cd ..` |
| `\` | Root of the drive or path separator on Windows | `C:\Projects` |