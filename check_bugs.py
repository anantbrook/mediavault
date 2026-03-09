#!/usr/bin/env python3
"""
check_bugs.py
─────────────
Scans all project files for bugs, syntax errors, and common issues.
Run this anytime: python3 scripts/check_bugs.py

Output is color-coded and easy to read.
"""

import ast
import os
import sys
import json
import re
import importlib.util
from pathlib import Path

ROOT = Path(__file__).parent.parent
COLORS = {
    "green":  "\033[92m",
    "red":    "\033[91m",
    "yellow": "\033[93m",
    "cyan":   "\033[96m",
    "bold":   "\033[1m",
    "reset":  "\033[0m",
}

def c(text, color):
    return f"{COLORS[color]}{text}{COLORS['reset']}"

def banner(text):
    line = "─" * 60
    print(f"\n{c(line, 'cyan')}")
    print(f"{c('  ' + text, 'bold')}")
    print(f"{c(line, 'cyan')}")


# ─────────────────────────────────────────────────────────────────────────────
#  CHECK PYTHON SYNTAX
# ─────────────────────────────────────────────────────────────────────────────
def check_python_syntax(path: Path) -> list[dict]:
    issues = []
    try:
        source = path.read_text(encoding="utf-8")
        ast.parse(source)
    except SyntaxError as e:
        issues.append({
            "file":    str(path.relative_to(ROOT)),
            "line":    e.lineno,
            "type":    "SyntaxError",
            "message": e.msg,
            "text":    e.text.strip() if e.text else "",
        })
    except Exception as e:
        issues.append({
            "file":    str(path.relative_to(ROOT)),
            "line":    0,
            "type":    "ParseError",
            "message": str(e),
            "text":    "",
        })
    return issues


# ─────────────────────────────────────────────────────────────────────────────
#  CHECK COMMON PYTHON BUGS
# ─────────────────────────────────────────────────────────────────────────────
COMMON_PATTERNS = [
    (r"except\s*:", "Bare except clause — catches ALL exceptions including KeyboardInterrupt"),
    (r"print\s*\(", None),  # OK — just count them
    (r"TODO|FIXME|HACK|XXX", "Unresolved TODO/FIXME in code"),
    (r"import \*", "Wildcard import — can cause name collisions"),
    (r"eval\s*\(", "eval() is dangerous — review this"),
    (r"exec\s*\(", "exec() is dangerous — review this"),
    (r"os\.system\s*\(", "os.system() — prefer subprocess"),
    (r"password\s*=\s*['\"][^'\"]+['\"]", "Hardcoded password detected!"),
    (r"api_key\s*=\s*['\"][^'\"]+['\"]", "Hardcoded API key detected!"),
    (r"secret\s*=\s*['\"][^'\"]+['\"]", "Hardcoded secret detected!"),
]

def check_code_smells(path: Path) -> list[dict]:
    issues = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
        for i, line in enumerate(lines, 1):
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            for pattern, message in COMMON_PATTERNS:
                if message and re.search(pattern, line, re.IGNORECASE):
                    issues.append({
                        "file":    str(path.relative_to(ROOT)),
                        "line":    i,
                        "type":    "Warning",
                        "message": message,
                        "text":    stripped[:80],
                    })
    except Exception as e:
        pass
    return issues


# ─────────────────────────────────────────────────────────────────────────────
#  CHECK HTML/JS SYNTAX
# ─────────────────────────────────────────────────────────────────────────────
def check_html(path: Path) -> list[dict]:
    issues = []
    try:
        content = path.read_text(encoding="utf-8")
        # Check for common JS syntax issues
        lines = content.splitlines()
        for i, line in enumerate(lines, 1):
            # Unclosed template literals
            ticks = line.count("`")
            if ticks % 2 != 0:
                issues.append({
                    "file": str(path.relative_to(ROOT)),
                    "line": i, "type": "Warning",
                    "message": "Odd number of backticks — possible unclosed template literal",
                    "text": line.strip()[:80],
                })
            # Mixing quote types
            if "font-family:'" in line and 'onclick="' in line:
                issues.append({
                    "file": str(path.relative_to(ROOT)),
                    "line": i, "type": "Warning",
                    "message": "Single-quote inside double-quoted attribute — may break JS",
                    "text": line.strip()[:80],
                })
    except Exception as e:
        pass
    return issues


# ─────────────────────────────────────────────────────────────────────────────
#  CHECK REQUIRED FILES EXIST
# ─────────────────────────────────────────────────────────────────────────────
REQUIRED_FILES = [
    "backend/app.py",
    "frontend/public/index.html",
    "modules/bypass_cloudflare.py",
    "modules/cookie_manager.py",
    "modules/download_engine.py",
    "modules/site_scrapers.py",
    "modules/media_analyzer.py",
    "modules/scheduler.py",
    "config/settings.json",
    "requirements.txt",
]

def check_required_files() -> list[dict]:
    missing = []
    for rel in REQUIRED_FILES:
        p = ROOT / rel
        if not p.exists():
            missing.append({
                "file": rel, "line": 0,
                "type": "Missing", "message": "Required file not found", "text": "",
            })
    return missing


# ─────────────────────────────────────────────────────────────────────────────
#  CHECK requirements.txt vs actual imports
# ─────────────────────────────────────────────────────────────────────────────
def check_imports() -> list[dict]:
    issues = []
    req_path = ROOT / "requirements.txt"
    if not req_path.exists():
        return [{"file": "requirements.txt", "line": 0,
                 "type": "Missing", "message": "requirements.txt not found", "text": ""}]

    declared = set()
    for line in req_path.read_text().splitlines():
        pkg = re.split(r"[><=!]", line.strip())[0].strip().lower().replace("-", "_")
        if pkg:
            declared.add(pkg)

    # Scan imports in backend/
    used = set()
    for py_file in (ROOT / "backend").glob("**/*.py"):
        for line in py_file.read_text().splitlines():
            m = re.match(r"^\s*(?:import|from)\s+([a-zA-Z_][a-zA-Z0-9_]*)", line)
            if m:
                used.add(m.group(1).lower())

    STDLIB = {"os","sys","re","json","time","threading","pathlib","typing","io",
              "struct","hashlib","subprocess","random","uuid","datetime","base64",
              "collections","functools","itertools","math","string","urllib",
              "http","socket","ssl","logging","traceback","copy","contextlib",
              "xml","email","html","csv","tempfile","shutil","glob","fnmatch",
              "ast","importlib","inspect","types","abc","enum","dataclasses",
              "weakref","gc","platform","signal","queue","multiprocessing"}

    third_party = used - STDLIB
    for pkg in third_party:
        if pkg not in declared and pkg not in {"app", "modules"}:
            issues.append({
                "file": "requirements.txt", "line": 0,
                "type": "Warning",
                "message": f"'{pkg}' used in code but may not be in requirements.txt",
                "text": "",
            })
    return issues


# ─────────────────────────────────────────────────────────────────────────────
#  MAIN RUNNER
# ─────────────────────────────────────────────────────────────────────────────
def run_all_checks():
    all_issues = []
    error_count = 0
    warn_count = 0

    print(c("\n⚡  MEDIA VAULT PRO — Bug Checker", "bold"))
    print(c(f"   Scanning: {ROOT}", "cyan"))

    # 1. Required files
    banner("1/5  Required Files")
    issues = check_required_files()
    for iss in issues:
        print(c(f"  ❌  MISSING: {iss['file']}", "red"))
        error_count += 1
    if not issues:
        print(c("  ✅  All required files present", "green"))
    all_issues.extend(issues)

    # 2. Python syntax
    banner("2/5  Python Syntax")
    py_files = list(ROOT.glob("**/*.py"))
    py_files = [f for f in py_files if ".git" not in str(f)]
    syntax_ok = 0
    for pf in py_files:
        issues = check_python_syntax(pf)
        for iss in issues:
            rel = str(pf.relative_to(ROOT))
            print(c(f"  ❌  {rel}:{iss['line']}", "red") + f"  {iss['message']}")
            if iss["text"]:
                print(f"      {c(iss['text'], 'yellow')}")
            error_count += 1
        if not issues:
            syntax_ok += 1
        all_issues.extend(issues)
    print(c(f"  ✅  {syntax_ok}/{len(py_files)} Python files OK", "green"))

    # 3. Code smells
    banner("3/5  Code Quality Warnings")
    smell_count = 0
    for pf in py_files:
        issues = check_code_smells(pf)
        for iss in issues:
            rel = str(pf.relative_to(ROOT))
            print(c(f"  ⚠️   {rel}:{iss['line']}", "yellow") + f"  {iss['message']}")
            warn_count += 1
            smell_count += 1
        all_issues.extend(issues)
    if smell_count == 0:
        print(c("  ✅  No code smells found", "green"))

    # 4. HTML/JS check
    banner("4/5  HTML / JavaScript")
    html_files = list(ROOT.glob("**/*.html"))
    for hf in html_files:
        issues = check_html(hf)
        for iss in issues:
            rel = str(hf.relative_to(ROOT))
            print(c(f"  ⚠️   {rel}:{iss['line']}", "yellow") + f"  {iss['message']}")
            warn_count += 1
        if not issues:
            print(c(f"  ✅  {hf.name} OK", "green"))
        all_issues.extend(issues)

    # 5. Import check
    banner("5/5  Dependencies")
    issues = check_imports()
    for iss in issues:
        print(c(f"  ⚠️   {iss['message']}", "yellow"))
        warn_count += 1
    if not issues:
        print(c("  ✅  All imports OK", "green"))
    all_issues.extend(issues)

    # Summary
    line = "═" * 60
    print(f"\n{c(line, 'cyan')}")
    print(c("  SUMMARY", "bold"))
    print(f"  {c(str(error_count), 'red')} errors    {c(str(warn_count), 'yellow')} warnings")
    print(f"  Total files scanned: {len(py_files)} Python, {len(html_files)} HTML")
    print(c(line, 'cyan'))

    # Write report
    report_path = ROOT / "scripts" / "bug_report.json"
    with open(report_path, "w") as f:
        json.dump({
            "errors": error_count,
            "warnings": warn_count,
            "issues": all_issues,
        }, f, indent=2)
    print(f"\n  📄  Full report: {c(str(report_path), 'cyan')}")

    return error_count == 0


if __name__ == "__main__":
    ok = run_all_checks()
    sys.exit(0 if ok else 1)
