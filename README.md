# 🔍 SonarQube Code Correction Agent

An interactive CLI tool that connects to a locally running SonarQube instance,
fetches all open code-quality issues, proposes minimal surgical fixes, and
applies them **only after explicit human approval**.

## ✨ Features

- **Automatic issue detection** — fetches OPEN / CONFIRMED / REOPENED issues
  from SonarQube, sorted by severity (BLOCKER first).
- **Rule-based fixers** — built-in handlers for common rules (unused imports,
  trailing whitespace, empty blocks) with extensible registry.
- **Human-in-the-loop** — every fix is shown as a unified diff and requires
  explicit approval before being written to disk.
- **Safe by default** — creates backups of every file before modification.
- **MCP support** — optional connection to SonarQube MCP server via stdio;
  falls back to REST API.
- **Beautiful terminal UI** — powered by Rich, with colored severity badges,
  syntax-highlighted diffs, and formatted tables.

## 🚀 Quick Start

### 1. Install dependencies

```bash
pip install -r requirements.txt
```

### 2. Configure

```bash
cp .env.example .env
```

Edit `.env` and fill in:

| Variable            | Description                                      |
|---------------------|--------------------------------------------------|
| `SONAR_HOST_URL`    | SonarQube base URL (default: `http://localhost:9000`) |
| `SONAR_TOKEN`       | Your SonarQube user token                        |
| `SONAR_PROJECT_KEY` | The project key as shown in SonarQube            |
| `PROJECT_PATH`      | Absolute path to your source code                |

### 3. Run

```bash
# Interactive mode — process issues one by one
python main.py

# Run sonar-scanner first, then process issues
python main.py --scan

# Preview fixes without applying (dry-run)
python main.py --dry-run

# Try MCP server before falling back to REST
python main.py --mcp
```

## 📁 Project Structure

```
sonar-project/
├── main.py                 # Entry point & orchestrator
├── config.py               # .env loader & validation
├── models.py               # Data classes (SonarIssue, ProposedFix, etc.)
├── sonar_client.py         # SonarQube REST API client
├── issue_processor.py      # Rule-based fixers & diff generation
├── file_manager.py         # File I/O & backup management
├── display.py              # Rich-powered terminal UI
├── mcp/
│   ├── __init__.py
│   └── sonar_mcp_client.py # MCP stdio transport client
├── requirements.txt
├── .env.example            # Configuration template
├── .gitignore
└── README.md
```

## 🔄 Workflow

```
┌──────────────┐     ┌──────────────┐     ┌──────────────┐
│  1. Connect  │────▶│  2. Fetch    │────▶│  3. Analyse  │
│  to SonarQube│     │  Issues      │     │  & Fix       │
└──────────────┘     └──────────────┘     └──────┬───────┘
                                                  │
                     ┌──────────────┐     ┌───────▼──────┐
                     │  5. Apply    │◀────│  4. Human    │
                     │  Fix         │ yes │  Approval    │
                     └──────┬───────┘     └──────────────┘
                            │
                     ┌──────▼───────┐
                     │  6. Summary  │
                     │  Report      │
                     └──────────────┘
```

## 🛡️ Safety

- **Backups** — Every modified file is backed up to `.sonar-backups/` before
  any write.
- **Minimal changes** — Fixers only touch what's necessary to resolve the
  specific SonarQube rule violation.
- **No auto-apply** — The agent never writes to disk without your explicit
  `yes` response.

## 🔧 Extending

To add a new fixer for a SonarQube rule:

1. Write a function in `issue_processor.py` matching the signature:
   ```python
   def _fix_my_rule(content: str, issue: SonarIssue, rule: dict) -> FixerResult:
       ...
   ```
2. Register it in the `FIXERS` dict:
   ```python
   FIXERS["S9999"] = _fix_my_rule
   ```

## 📝 License

MIT
