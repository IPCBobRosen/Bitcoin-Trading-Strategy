# Git Cheat Sheet

This document contains the Git commands used throughout the BTS project.

It will grow as the project grows.

git init
git status
git add .
git commit
## Basic Git Workflow

### Check the project status

```powershell
git status
```

Shows:

- New files
- Modified files
- Deleted files
- Staged files
- Unstaged files
- The current Git branch

---

### Stage project changes

```powershell
git add .
```

The period (`.`) means the current directory.

This command stages changes in the current directory and its subfolders,
except files and folders excluded by `.gitignore`.

Staging prepares files for the next commit. It does not yet create a permanent
Git snapshot.

---

### Verify the staged changes

```powershell
git status
```

Run `git status` again before committing to verify exactly which files will be
included in the next snapshot.