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


## Why run `git add .` more than once?

It is perfectly normal to edit files after they have already been staged.

When this happens:

```text
Edit file
↓
git add .
↓
Edit file again
↓
git status
```

Git will report:

- Changes to be committed
- Changes not staged for commit

Simply run:

```powershell
git add .
```

again to update the staging area with the latest version of every file.


## Creating a Git Repository

### Command

```powershell
git init
```

### Purpose

Creates a new Git repository in the current directory.

### What happens?

Git creates a hidden folder named:

```text
.git
```

This folder contains the project's complete version history, configuration,
staging information, branches, and commit database.

The presence of the `.git` folder tells Git that the directory is a Git
repository.

### Example

```powershell
cd C:\Projects\BitcoinTradingSystem
git init
```

After running this command, Git begins managing the BTS project.


## Rename the Current Branch

### Command

```powershell
git branch -M main
```

### Purpose

Renames the current Git branch to `main`.

### Why?

Modern Git repositories typically use `main` as the default branch instead of
`master`.

### Verify

```powershell
git branch
```

The active branch is indicated with an asterisk (`*`).

Example:

```text
* main
```

# Protocol

A protocol is an agreed-upon set of rules that defines how two systems
communicate.

A protocol specifies:

- Message format
- Required fields
- Data types
- Message order
- Error handling
- Acknowledgments

Good protocols eliminate ambiguity.

Example:

Instead of sending:

BUY

A protocol defines a complete message such as:

- Signal
- Symbol
- Quantity
- Timestamp
- Strategy
- Environment