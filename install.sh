#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
#  Nyx — installer / updater
#
#  Smart, idempotent, OS-aware.  Run it twice and nothing breaks; it'll
#  just bring you up to date.
#
#  One-liner (no clone needed):
#    curl -fsSL https://raw.githubusercontent.com/the-priest/nyxui/main/install.sh | bash
#
#  From a clone:
#    git clone https://github.com/the-priest/nyxui.git && cd nyxui && ./install.sh
#
#  Detects:
#    · Debian / Ubuntu / Kali / Linux Mint / Pop / Raspbian
#    · Arch / Manjaro / EndeavourOS
#    · Fedora / RHEL / Rocky / Alma
#    · openSUSE
#    · macOS (via Homebrew)
#    · Termux on Android
#
#  Handles:
#    · Missing python3 / pip / git / curl  →  installs them
#    · Missing pip packages (groq, flask)  →  installs with PEP-668 fallback
#    · Existing install                    →  backs up memory.db, then updates
#    · Pantheon binaries (zeus/ares/hades) →  reports presence, doesn't fail
#    · GROQ_API_KEY                        →  warns if missing
#
#  Flags:
#    --uninstall    remove launchers + .desktop (keeps ~/.nyx data)
#    --purge        remove everything including ~/.nyx (asks first)
#    --force        reinstall even if already up to date
#    --branch <b>   pull from a non-main branch
# ─────────────────────────────────────────────────────────────────────────────

set -e

REPO_URL_DEFAULT="https://github.com/the-priest/nyxui.git"
RAW_URL_DEFAULT="https://raw.githubusercontent.com/the-priest/nyxui"
REPO_URL="${NYX_REPO_URL:-$REPO_URL_DEFAULT}"
RAW_URL="${NYX_RAW_URL:-$RAW_URL_DEFAULT}"
BRANCH="main"

INSTALL_DIR="$HOME/.local/share/nyxui"
BIN_DIR="$HOME/.local/bin"
DESKTOP_DIR="$HOME/.local/share/applications"
ICON_DIR="$HOME/.local/share/icons/hicolor/scalable/apps"

MODE_UNINSTALL=0
MODE_PURGE=0
MODE_FORCE=0

while [ $# -gt 0 ]; do
  case "$1" in
    --uninstall) MODE_UNINSTALL=1 ;;
    --purge)     MODE_PURGE=1; MODE_UNINSTALL=1 ;;
    --force)     MODE_FORCE=1 ;;
    --branch)    BRANCH="$2"; shift ;;
    -h|--help)
      sed -n '2,30p' "$0" 2>/dev/null || true
      exit 0 ;;
    *) echo "unknown flag: $1"; exit 1 ;;
  esac
  shift
done

# ─── output helpers ──────────────────────────────────────────────────────────
if [ -t 1 ]; then
  R=$'\033[31m'; G=$'\033[32m'; Y=$'\033[33m'; C=$'\033[36m'
  D=$'\033[90m'; N=$'\033[94m'; P=$'\033[35m'; B=$'\033[1m'; X=$'\033[0m'
else
  R=""; G=""; Y=""; C=""; D=""; N=""; P=""; B=""; X=""
fi
hdr() { printf "\n%s\n" "${N}━━━ $* ━━━${X}"; }
say() { printf "%s\n" "${N}▸${X} $*"; }
ok()  { printf "%s\n" "${G}  ✓${X} $*"; }
warn(){ printf "%s\n" "${Y}  ⚠${X} $*"; }
err() { printf "%s\n" "${R}  ✕${X} $*"; }
dim() { printf "%s\n" "${D}    $*${X}"; }
die() { err "$*"; exit 1; }

banner() {
  printf "\n"
  printf "%s\n" "${N}     ✦${X}"
  printf "%s\n" "    ${P}nyx${X}  ${D}— primordial goddess of night${X}"
  printf "%s\n" "${D}    installer · updater${X}"
  printf "\n"
}

# ─── detection ───────────────────────────────────────────────────────────────
detect_os() {
  ENV_OS="unknown"
  ENV_PKG=""
  if [ -n "${TERMUX_VERSION:-}" ] || [ "$(uname -o 2>/dev/null)" = "Android" ]; then
    ENV_OS="termux"; ENV_PKG="pkg"
  elif [ "$(uname -s)" = "Darwin" ]; then
    ENV_OS="macos"; ENV_PKG="brew"
  elif [ -f /etc/os-release ]; then
    # shellcheck disable=SC1091
    . /etc/os-release
    case "${ID:-}${ID_LIKE:-}" in
      *debian*|*ubuntu*|*kali*|*mint*|*raspbian*|*pop*|*elementary*)
        ENV_OS="debian"; ENV_PKG="apt" ;;
      *arch*|*manjaro*|*endeavouros*)
        ENV_OS="arch"; ENV_PKG="pacman" ;;
      *fedora*|*rhel*|*centos*|*rocky*|*alma*)
        ENV_OS="fedora"; ENV_PKG="dnf" ;;
      *suse*|*opensuse*)
        ENV_OS="suse"; ENV_PKG="zypper" ;;
      *) ENV_OS="linux-other"; ENV_PKG="" ;;
    esac
  fi
  ok "detected: ${B}$ENV_OS${X} ${D}(pkg manager: ${ENV_PKG:-none})${X}"
}

detect_mode() {
  if [ -d "$INSTALL_DIR/.git" ] || [ -x "$BIN_DIR/nyx-app" ] || [ -d "$INSTALL_DIR" ]; then
    INSTALL_MODE="update"
    ok "existing install found → ${B}update${X} mode"
  else
    INSTALL_MODE="install"
    ok "no existing install → ${B}install${X} mode"
  fi
}

# ─── privilege ───────────────────────────────────────────────────────────────
SUDO=""
ensure_sudo() {
  if [ "$ENV_OS" = "termux" ] || [ "$(id -u)" = "0" ]; then
    SUDO=""
    return
  fi
  if command -v sudo >/dev/null 2>&1; then
    SUDO="sudo"
  else
    SUDO=""
  fi
}

# ─── deps: python, pip, git, curl ────────────────────────────────────────────
have() { command -v "$1" >/dev/null 2>&1; }

check_python_version() {
  if ! have python3; then return 1; fi
  python3 - <<'PY' >/dev/null 2>&1 || return 1
import sys
sys.exit(0 if sys.version_info >= (3, 10) else 1)
PY
}

install_system_deps() {
  local need=()
  have python3       || need+=("python3")
  have pip3 || python3 -m pip --version >/dev/null 2>&1 || need+=("pip")
  have git           || need+=("git")
  have curl          || need+=("curl")

  if [ ${#need[@]} -eq 0 ] && check_python_version; then
    ok "python3 / pip / git / curl all present"
    return
  fi

  say "installing system deps: ${need[*]:-(python upgrade)}"
  case "$ENV_OS" in
    debian)
      $SUDO apt-get update -qq
      $SUDO apt-get install -y python3 python3-pip python3-venv git curl \
        >/dev/null 2>&1 || die "apt-get install failed"
      ;;
    arch)
      $SUDO pacman -Sy --noconfirm --needed python python-pip git curl \
        >/dev/null 2>&1 || die "pacman install failed"
      ;;
    fedora)
      $SUDO dnf install -y python3 python3-pip git curl \
        >/dev/null 2>&1 || die "dnf install failed"
      ;;
    suse)
      $SUDO zypper -n install python3 python3-pip git curl \
        >/dev/null 2>&1 || die "zypper install failed"
      ;;
    termux)
      pkg update -y >/dev/null 2>&1 || true
      pkg install -y python git curl \
        >/dev/null 2>&1 || die "pkg install failed"
      ;;
    macos)
      have brew || die "homebrew not installed (https://brew.sh)"
      brew install python git >/dev/null 2>&1 || warn "brew install reported issues"
      ;;
    *)
      warn "unknown OS — install python3 (>=3.10), pip, git, curl manually"
      ;;
  esac

  check_python_version || die "python3 >=3.10 required after install"
  ok "system deps OK"
}

# ─── pip install with PEP-668 fallback chain ─────────────────────────────────
pip_install() {
  local pkg="$1"
  local out

  if python3 -m pip show "$pkg" >/dev/null 2>&1; then
    ok "$pkg already installed"
    return 0
  fi

  for flags in "" "--user" "--break-system-packages" "--user --break-system-packages"; do
    if out=$(python3 -m pip install $flags "$pkg" 2>&1); then
      ok "$pkg installed${flags:+ ($flags)}"
      return 0
    fi
  done

  err "could not install $pkg via pip"
  printf "%s\n" "$out" | tail -5 | sed "s/^/    /"
  return 1
}

install_python_deps() {
  say "installing python deps (groq, flask)..."
  pip_install groq  || die "groq install failed — nyx can't think without it"
  pip_install flask || die "flask install failed — web ui can't run without it"
}

# ─── source: clone, pull, or use local checkout ──────────────────────────────
fetch_sources() {
  # Are we already in a clone?  i.e. is install.sh sitting next to nyx.py?
  local script_dir=""
  if [ -n "${BASH_SOURCE[0]:-}" ]; then
    script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" 2>/dev/null && pwd)" || script_dir=""
  fi

  if [ -n "$script_dir" ] && [ -f "$script_dir/nyx.py" ] && [ -f "$script_dir/nyx_web.py" ]; then
    SOURCE_DIR="$script_dir"
    ok "using local checkout: $SOURCE_DIR"
    return
  fi

  # Otherwise, clone or update the repo at INSTALL_DIR
  mkdir -p "$(dirname "$INSTALL_DIR")"

  if [ -d "$INSTALL_DIR/.git" ]; then
    say "updating repo at $INSTALL_DIR (branch: $BRANCH)..."
    backup_memory
    git -C "$INSTALL_DIR" fetch --quiet origin
    git -C "$INSTALL_DIR" checkout --quiet "$BRANCH"
    git -C "$INSTALL_DIR" reset --hard --quiet "origin/$BRANCH"
    ok "repo updated"
  elif [ -d "$INSTALL_DIR" ]; then
    warn "$INSTALL_DIR exists but is not a git repo — re-cloning"
    rm -rf "$INSTALL_DIR"
    say "cloning $REPO_URL..."
    git clone --quiet --branch "$BRANCH" "$REPO_URL" "$INSTALL_DIR" \
      || die "git clone failed — check the repo URL: $REPO_URL"
    ok "cloned"
  else
    say "cloning $REPO_URL..."
    git clone --quiet --branch "$BRANCH" "$REPO_URL" "$INSTALL_DIR" \
      || die "git clone failed — check the repo URL: $REPO_URL"
    ok "cloned"
  fi
  SOURCE_DIR="$INSTALL_DIR"
}

backup_memory() {
  local db="$HOME/.nyx/memory.db"
  [ -f "$db" ] || return 0
  local bdir="$HOME/.nyx/backups"
  mkdir -p "$bdir"
  local ts; ts="$(date +%Y%m%d-%H%M%S)"
  cp "$db" "$bdir/memory-$ts.db"
  ok "backed up memory.db → $bdir/memory-$ts.db"
}

# ─── launchers + desktop entry + icon ────────────────────────────────────────
install_launchers() {
  mkdir -p "$BIN_DIR"

  # Terminal launcher (her original CLI)
  ln -sf "$SOURCE_DIR/nyx.py" "$BIN_DIR/nyx"
  chmod +x "$SOURCE_DIR/nyx.py"
  ok "linked $BIN_DIR/nyx → nyx.py"

  # GUI launcher script — starts the server, opens a chromeless window,
  # tears the server down on close
  local app_browser="" app_flag=""
  for cand in chromium chromium-browser brave-browser google-chrome chrome \
              microsoft-edge edge firefox firefox-esr; do
    if have "$cand"; then
      app_browser="$cand"
      case "$cand" in
        firefox*) app_flag="" ;;          # Firefox has no real app-mode flag
        *)        app_flag="--app=" ;;
      esac
      break
    fi
  done

  cat > "$BIN_DIR/nyx-app" <<EOF
#!/usr/bin/env bash
# Auto-generated by install.sh — launches the Nyx web UI.
#
# usage:
#   nyx-app             open the chat UI
#   nyx-app --debug     run the server in the foreground (don't open browser)
#                       — use this if the app crashes; you'll see the error
#   nyx-app --log       tail the last server log
#
# env:
#   NYX_PORT   port to bind (default 5174)
#   NYX_HOME   where her memory lives (default ~/.nyx)

PORT="\${NYX_PORT:-5174}"
URL="http://127.0.0.1:\$PORT"
LOG="\${NYX_HOME:-\$HOME/.nyx}/nyx-app.log"
mkdir -p "\$(dirname "\$LOG")"

case "\${1:-}" in
  --debug)
    echo "running nyx_web.py in foreground on \$URL (Ctrl-C to stop)"
    exec python3 "$SOURCE_DIR/nyx_web.py" --port "\$PORT"
    ;;
  --log)
    [ -f "\$LOG" ] && tail -n 200 "\$LOG" || echo "no log yet: \$LOG"
    exit 0
    ;;
esac

# Start the server, mirror its output to a log file (not /dev/null —
# if she crashes you'll want to see what happened)
python3 "$SOURCE_DIR/nyx_web.py" --port "\$PORT" >"\$LOG" 2>&1 &
PID=\$!
trap 'kill \$PID 2>/dev/null' EXIT INT TERM

# Wait for the server to come up (max ~5s)
ready=0
for _ in 1 2 3 4 5 6 7 8 9 10; do
  if command -v curl >/dev/null 2>&1 && curl -fs "\$URL" >/dev/null 2>&1; then
    ready=1; break
  fi
  if ! kill -0 \$PID 2>/dev/null; then
    echo "nyx_web.py crashed during startup.  log:"
    tail -n 30 "\$LOG" 2>/dev/null
    exit 1
  fi
  sleep 0.4
done

if [ "\$ready" = "0" ]; then
  echo "server didn't respond at \$URL.  log:"
  tail -n 30 "\$LOG" 2>/dev/null
  exit 1
fi
EOF

  if [ -n "$app_browser" ] && [ -n "$app_flag" ]; then
    printf '%s\n' "$app_browser ${app_flag}\"\$URL\" >/dev/null 2>&1 || xdg-open \"\$URL\" 2>/dev/null" >> "$BIN_DIR/nyx-app"
  elif [ -n "$app_browser" ]; then
    printf '%s\n' "$app_browser \"\$URL\" >/dev/null 2>&1 || xdg-open \"\$URL\" 2>/dev/null" >> "$BIN_DIR/nyx-app"
  else
    printf '%s\n' "xdg-open \"\$URL\" 2>/dev/null || echo \"Open: \$URL\"" >> "$BIN_DIR/nyx-app"
  fi

  printf '\nwait $PID\n' >> "$BIN_DIR/nyx-app"
  chmod +x "$BIN_DIR/nyx-app"

  if [ -n "$app_browser" ]; then
    ok "launcher: $BIN_DIR/nyx-app ${D}(via $app_browser${app_flag:+ --app})${X}"
  else
    ok "launcher: $BIN_DIR/nyx-app ${D}(no browser detected — xdg-open fallback)${X}"
  fi

  # PATH check
  case ":$PATH:" in
    *":$BIN_DIR:"*) ok "$BIN_DIR is on PATH" ;;
    *)
      warn "$BIN_DIR is NOT on PATH"
      dim "add to your shell rc:  export PATH=\"\$HOME/.local/bin:\$PATH\""
      ;;
  esac
}

install_icon_and_desktop() {
  # No app grid on Termux
  if [ "$ENV_OS" = "termux" ]; then
    dim "termux: skipping .desktop entry (no app grid)"
    return
  fi

  local hicolor="$HOME/.local/share/icons/hicolor"

  # SVG → scalable theme dir
  if [ -f "$SOURCE_DIR/icon.svg" ]; then
    mkdir -p "$hicolor/scalable/apps"
    cp "$SOURCE_DIR/icon.svg" "$hicolor/scalable/apps/nyx.svg"
    ok "icon: $hicolor/scalable/apps/nyx.svg"
  fi

  # PNGs at standard sizes → hicolor/{SIZE}x{SIZE}/apps/nyx.png
  local sizes="16 24 32 48 64 128 256 512"
  local installed_png=""
  for sz in $sizes; do
    local src="$SOURCE_DIR/icons/icon-${sz}.png"
    if [ -f "$src" ]; then
      mkdir -p "$hicolor/${sz}x${sz}/apps"
      cp "$src" "$hicolor/${sz}x${sz}/apps/nyx.png"
      installed_png="$hicolor/${sz}x${sz}/apps/nyx.png"
    fi
  done
  if [ -n "$installed_png" ]; then
    ok "icons (PNG): 16→512 sizes installed"
  fi

  # Pick the canonical absolute path for the .desktop Icon= field.
  # Prefer 256 PNG (most launchers handle this best); fall back gracefully.
  local icon_path=""
  for candidate in "$hicolor/256x256/apps/nyx.png" \
                   "$hicolor/128x128/apps/nyx.png" \
                   "$hicolor/512x512/apps/nyx.png" \
                   "$hicolor/scalable/apps/nyx.svg"; do
    if [ -f "$candidate" ]; then
      icon_path="$candidate"
      break
    fi
  done
  # Fall back to theme-name lookup if no file was installed
  [ -z "$icon_path" ] && icon_path="nyx"

  mkdir -p "$DESKTOP_DIR"
  cat > "$DESKTOP_DIR/nyx.desktop" <<EOF
[Desktop Entry]
Type=Application
Version=1.0
Name=Nyx
GenericName=Learning agent
Comment=primordial goddess of night — local chat with an evolving agent
Exec=$BIN_DIR/nyx-app
Icon=$icon_path
Terminal=false
Categories=Utility;Development;Network;
Keywords=nyx;ai;agent;chat;groq;security;
StartupNotify=true
EOF
  chmod +x "$DESKTOP_DIR/nyx.desktop" 2>/dev/null || true
  ok ".desktop: $DESKTOP_DIR/nyx.desktop  ${D}(Icon=$icon_path)${X}"

  # Refresh caches (best-effort)
  if have update-desktop-database; then
    update-desktop-database "$DESKTOP_DIR" >/dev/null 2>&1 || true
  fi
  if have gtk-update-icon-cache; then
    gtk-update-icon-cache -q -t -f "$hicolor" >/dev/null 2>&1 || true
  fi
  if have xdg-desktop-menu; then
    xdg-desktop-menu forceupdate >/dev/null 2>&1 || true
  fi
}

# ─── pantheon + groq env check ───────────────────────────────────────────────
check_pantheon() {
  for tool in zeus ares hades; do
    if have "$tool"; then ok "pantheon: $tool"
    else dim "pantheon: $tool not on PATH (optional)"
    fi
  done
}

check_groq_key() {
  if [ -n "${GROQ_API_KEY:-}" ]; then
    ok "GROQ_API_KEY is set in your shell"
  elif [ -f "$HOME/.nyx/config.json" ] && grep -q gsk_ "$HOME/.nyx/config.json" 2>/dev/null; then
    ok "GROQ_API_KEY is saved at ~/.nyx/config.json"
  else
    dim "GROQ_API_KEY not set — the app will prompt for one on first launch"
    dim "(get a free key at https://console.groq.com)"
  fi
}

# ─── uninstall path ──────────────────────────────────────────────────────────
do_uninstall() {
  hdr "uninstalling"
  rm -f "$BIN_DIR/nyx" "$BIN_DIR/nyx-app"
  rm -f "$DESKTOP_DIR/nyx.desktop"
  rm -f "$ICON_DIR/nyx.svg"
  if [ -d "$INSTALL_DIR" ]; then
    rm -rf "$INSTALL_DIR"
    ok "removed $INSTALL_DIR"
  fi
  have update-desktop-database && update-desktop-database "$DESKTOP_DIR" >/dev/null 2>&1 || true
  ok "launchers + desktop entry removed"

  if [ "$MODE_PURGE" = "1" ]; then
    if [ -d "$HOME/.nyx" ]; then
      printf "%s" "${R}  ⚠ this will delete ALL of Nyx's memory at ~/.nyx — type ${B}YES${X}${R} to confirm: ${X}"
      read -r ans
      if [ "$ans" = "YES" ]; then
        rm -rf "$HOME/.nyx"
        ok "removed ~/.nyx (memory wiped)"
      else
        dim "kept ~/.nyx — re-run with --purge to remove later"
      fi
    fi
  else
    dim "kept ~/.nyx — use --purge to also wipe memory"
  fi
  echo
  exit 0
}

# ─── main flow ───────────────────────────────────────────────────────────────
main() {
  banner

  hdr "detecting environment"
  detect_os
  detect_mode
  ensure_sudo

  if [ "$MODE_UNINSTALL" = "1" ]; then
    do_uninstall
  fi

  hdr "system dependencies"
  install_system_deps

  hdr "sources"
  fetch_sources

  hdr "python dependencies"
  install_python_deps

  hdr "launchers + app entry"
  install_launchers
  install_icon_and_desktop

  hdr "checks"
  check_pantheon
  check_groq_key

  echo
  printf "%s\n" "${G}  ✦ done.${X}"
  echo
  if [ "$INSTALL_MODE" = "update" ]; then
    printf "%s\n" "  updated to latest.  your memory at ~/.nyx is unchanged."
  else
    printf "%s\n" "  she is installed.  run:"
    printf "%s\n" "    ${N}nyx-app${X}              ${D}# friendly chat ui${X}"
    printf "%s\n" "    ${N}nyx${X}                  ${D}# terminal repl${X}"
    printf "%s\n" "  or launch ${B}Nyx${X} from your app grid."
  fi
  echo
  dim "uninstall:  $0 --uninstall"
  dim "purge:      $0 --purge       (also wipes ~/.nyx)"
  dim "update:     just re-run this script"
  echo
}

main "$@"
