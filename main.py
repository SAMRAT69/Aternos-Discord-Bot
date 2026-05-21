import subprocess
import sys
import os

print("=" * 55)
print("  Aternos Discord Bot — Launcher")
print("=" * 55)

# ── 1. Check Python version ────────────────────────────────
major, minor = sys.version_info[:2]
print(f"\n[1/4] Python {major}.{minor} detected", flush=True)
if (major, minor) < (3, 9):
    print("ERROR: Python 3.9+ is required.")
    sys.exit(1)

# ── 2. Validate required environment variables ─────────────
print("\n[2/4] Checking environment variables ...", flush=True)
required = {
    "DISCORD_TOKEN":   "Your Discord bot token",
    "ATERNOS_USER":    "Your Aternos username",
    "ATERNOS_PASS":    "Your Aternos password",
}
missing = []
for var, desc in required.items():
    val = os.environ.get(var, "").strip()
    if val:
        masked = val[:6] + "*" * max(0, len(val) - 6)
        print(f"  ✔  {var} = {masked}", flush=True)
    else:
        print(f"  ✘  {var} not set  ({desc})", flush=True)
        missing.append(var)

if missing:
    print(
        "\n  WARNING: Some environment variables are missing.\n"
        "  The bot will fall back to the hardcoded values inside aternos_server_bot.py.\n"
        "  Set them in your Pterodactyl panel (Variables tab) for better security.\n",
        flush=True,
    )

# ── 3. Install / upgrade dependencies ─────────────────────
print("\n[3/4] Installing dependencies from requirements.txt ...", flush=True)
req_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "requirements.txt")

def pip_install(*extra_flags):
    cmd = [
        sys.executable, "-m", "pip", "install",
        "-r", req_file,
        "--quiet",
        "--disable-pip-version-check",
        *extra_flags,
    ]
    return subprocess.run(cmd, capture_output=True, text=True)

result = pip_install()

if result.returncode != 0:
    stderr = result.stderr or ""
    if "externally-managed-environment" in stderr:
        # Nix / Replit environment
        print("  Managed environment detected — retrying with --break-system-packages ...", flush=True)
        result = pip_install("--break-system-packages")
    elif "Permission denied" in stderr or "permission" in stderr.lower():
        # Shared system Python without write access
        print("  Permission error detected — retrying with --user ...", flush=True)
        result = pip_install("--user")

if result.returncode != 0:
    print("pip stdout:\n", result.stdout)
    print("pip stderr:\n", result.stderr)
    print("ERROR: Failed to install dependencies. See above.")
    sys.exit(1)

print("  All packages installed successfully.", flush=True)

# ── 4. Patch js2py for Python 3.10+ compatibility ─────────
print("\n[4/4] Applying compatibility patches ...", flush=True)
try:
    import importlib.util
    import pathlib

    spec = importlib.util.find_spec("js2py")
    if spec and spec.origin:
        import re
        injector = pathlib.Path(spec.origin).parent / "utils" / "injector.py"
        source   = injector.read_text(encoding="utf-8")

        # Canonical clean form — exactly what we want the file to contain.
        clean = (
            "try:\n"
            "    check(six.get_function_code(check))\n"
            "except (RuntimeError, ValueError):\n"
            "    pass\n"
        )

        if "check(six.get_function_code(check))" not in source:
            print("  js2py: target line not found — skipping.", flush=True)
        else:
            # Regex normalises ALL states in one pass:
            #   • bare call (unpatched)
            #   • correctly wrapped in try/except  (already patched)
            #   • broken double-wrapped try/try    (corrupted by old patcher)
            # It matches: optional leading "try:\n" lines, the call itself
            # (with any indentation), then optional trailing except/pass lines.
            pattern = re.compile(
                r"(?:[ \t]*try:[ \t]*\r?\n)*"
                r"[ \t]*check\(six\.get_function_code\(check\)\)[ \t]*\r?\n"
                r"(?:[ \t]*except[^\r\n]*:[ \t]*\r?\n[ \t]*pass[ \t]*(?:\r?\n)?)*"
            )
            fixed = pattern.sub(clean, source)
            if fixed == source:
                print("  js2py: already in clean state — skipping.", flush=True)
            else:
                injector.write_text(fixed, encoding="utf-8")
                print("  js2py patch applied/repaired.", flush=True)
    else:
        print("  js2py not found — skipping patch.", flush=True)
except Exception as e:
    print(f"  Patch warning (non-fatal): {e}", flush=True)

# ── Launch ─────────────────────────────────────────────────
print("\n" + "=" * 55)
print("  Starting bot ...")
print("=" * 55 + "\n", flush=True)

bot_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "aternos_server_bot.py")
os.execv(sys.executable, [sys.executable, bot_path])
