import os
import subprocess
import shutil
from pathlib import Path
from fastmcp import FastMCP

from sonar_agent.core import config
from sonar_agent.core import file_manager

mcp = FastMCP("GitHub Workspace Server")


@mcp.tool()
def setup_workspace(repo_url: str, branch: str) -> str:
    """Clones the repo (if needed) and creates a working branch (e.g., agent-sec-fixes)."""
    cwd = config.PROJECT_PATH
    
    # We assume the directory is already configured in PROJECT_PATH in the context of the user.
    git_dir = os.path.join(cwd, ".git")
    if not os.path.exists(git_dir):
        return f"Warning: {cwd} is not a Git repository. Cannot create a branch."

    subprocess.run(["git", "checkout", "-b", branch], cwd=cwd, capture_output=True, text=True)
    return f"Workspace setup complete. Switched to branch: {branch}"


@mcp.tool()
def read_file(file_path: str) -> str:
    """Reads vulnerable / source code from the workspace."""
    try:
        return file_manager.read_source_file(file_path)
    except Exception as e:
        return f"Error reading file: {str(e)}"


@mcp.tool()
def write_file(file_path: str, content: str) -> str:
    """Applies refactored code to the file and backs it up first."""
    try:
        backup = file_manager.create_backup(file_path)
        file_manager.write_fixed_file(file_path, content)
        return f"File '{file_path}' successfully rewritten. Backup saved to '{backup}'"
    except Exception as e:
        return f"Error writing file: {str(e)}"


@mcp.tool()
def revert_file(file_path: str) -> str:
    """Reverts a specific file to its baseline state (crucial for the HITL rejection flow)."""
    try:
        backup_dir = Path(config.PROJECT_PATH) / config.BACKUP_DIR
        safe_name = file_path.replace("/", "__").replace("\\", "__")
        
        # Find all matching backups
        possible_backups = list(backup_dir.glob(f"{safe_name}*bak"))
        if not possible_backups:
            # Fallback to git checkout
            res = subprocess.run(["git", "checkout", "--", file_path], cwd=config.PROJECT_PATH, capture_output=True, text=True)
            if res.returncode == 0:
                return f"No file backup found, reverted '{file_path}' using git checkout."
            return f"Error: No backup found for '{file_path}' to revert."

        # Find the latest modified backup
        latest_backup = sorted(possible_backups, key=lambda p: p.stat().st_mtime)[-1]
        source = Path(config.PROJECT_PATH) / file_path
        shutil.copy2(latest_backup, source)
        return f"File '{file_path}' successfully reverted to baseline from backup '{latest_backup}'."
    except Exception as e:
        return f"Error reverting file: {str(e)}"


@mcp.tool()
def commit_and_push(branch: str, message: str) -> str:
    """Finalizes accepted changes and pushes to the remote."""
    cwd = config.PROJECT_PATH
    
    res_add = subprocess.run(["git", "add", "."], cwd=cwd, capture_output=True, text=True)
    if res_add.returncode != 0:
        return f"Error adding files: {res_add.stderr}"

    res_commit = subprocess.run(["git", "commit", "-m", message], cwd=cwd, capture_output=True, text=True)
    if res_commit.returncode != 0:
        return f"No changes to commit or commit error (Code {res_commit.returncode}): {res_commit.stderr or res_commit.stdout}"
        
    res_push = subprocess.run(["git", "push", "-u", "origin", branch], cwd=cwd, capture_output=True, text=True)
    if res_push.returncode != 0:
        return f"Error pushing to remote: {res_push.stderr or res_push.stdout}"
        
    return f"Successfully committed and pushed to branch: {branch}"


if __name__ == "__main__":
    mcp.run()
