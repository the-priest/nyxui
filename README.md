<p align="center">
  <img src="icon.svg" width="140" alt="nyx">
</p>

<h1 align="center">nyx</h1>

<p align="center">
  <em>primordial goddess of night, mother of dreams</em><br>
  <sub>a local learning agent with mood, fatigue, curiosity, and a four-layer memory architecture — now with a friendly chat UI</sub>
</p>

<p align="center">
  <a href="#install"><img src="https://img.shields.io/badge/install-one--liner-6ea8ff?style=flat-square" alt="one-liner install"></a>
  <img src="https://img.shields.io/badge/python-3.10+-7e96c2?style=flat-square" alt="python 3.10+">
  <img src="https://img.shields.io/badge/inference-Groq-b08dd9?style=flat-square" alt="groq">
  <img src="https://img.shields.io/badge/ui-web+terminal-cdd6ec?style=flat-square" alt="web + terminal">
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-9aa0aa?style=flat-square" alt="MIT"></a>
</p>

---

## what it is

**Nyx** is a Groq-backed learning agent that lives on your machine and grows from your conversations. She has:

- a four-layer memory architecture — **hippocampus → episodic → semantic → procedural** (consolidation happens while she sleeps)
- **mood**, **fatigue**, and **curiosity** that drift over time
- a **theory of mind** about her operator
- **periodic self-reflection** every 100 interactions
- a **constitution** she cannot edit at runtime — only you can, by editing the source
- a chat UI on `127.0.0.1` you can run as a real app, plus the original terminal REPL

She's experimental. Day one she's blank. Useful behavior emerges after ~24–48 h of real use.

---

## install

Run this. It detects your distro, installs anything missing, and sets her up:

```
curl -fsSL https://raw.githubusercontent.com/the-priest/nyxui/main/install.sh | bash
```

That's it. Launch from your app grid as **Nyx**, or run `nyx-app` in a terminal. On first launch the app will prompt for your Groq API key (free at <https://console.groq.com>) and save it to `~/.nyx/config.json` (chmod 600). If you'd rather set it via shell env, you can:

```
export GROQ_API_KEY=gsk_...
```

— env always wins over the saved config. To change/remove the saved key later, type `/key` in the chat.

### what the installer does

- detects **Debian / Ubuntu / Kali / Mint / Pop**, **Arch / Manjaro**, **Fedora / RHEL / Rocky**, **openSUSE**, **macOS**, **Termux**
- checks for `python3 (≥3.10)`, `pip`, `git`, `curl` — installs whichever are missing
- installs `groq` and `flask` (with PEP-668 fallback chain: plain → `--user` → `--break-system-packages`)
- clones the repo to `~/.local/share/nyxui/`
- creates two launchers in `~/.local/bin/`:
  - `nyx` — terminal REPL (original)
  - `nyx-app` — friendly web UI in a chromeless browser window
- installs the icon to `~/.local/share/icons/` and a `.desktop` entry so **Nyx** shows up in your app grid
- detects pantheon binaries (`zeus`, `ares`, `hades`) — uses them if present, skips if not
- warns if `GROQ_API_KEY` is unset

---

## update

Re-run the installer. It detects the existing install, backs up `~/.nyx/memory.db` to `~/.nyx/backups/memory-<timestamp>.db`, then pulls the latest code:

```
curl -fsSL https://raw.githubusercontent.com/the-priest/nyxui/main/install.sh | bash
```

Or from a local clone:

```
cd ~/.local/share/nyxui && git pull && ./install.sh
```

---

## uninstall

Remove launchers and desktop entry, keep her memory:

```
~/.local/share/nyxui/install.sh --uninstall
```

Nuke everything including `~/.nyx`:

```
~/.local/share/nyxui/install.sh --purge
```

---

## the chat UI

Open the app and just talk to her. The input accepts free text (talks to Nyx) and slash commands (system queries):

| command | what it does |
|---|---|
| `/state` | mood · fatigue · curiosity |
| `/census` | counts in each memory layer |
| `/episodes` | last 10 consolidated episodes |
| `/know` | learned semantic patterns |
| `/reflex` | compiled procedural reflexes |
| `/prefs` | preferences she's developed |
| `/tom` | what she's observed about you |
| `/reflections` | her self-observations |
| `/dreams` | consolidation log |
| `/sleep` | force a consolidation cycle now |
| `/reflect` | force a reflection now |
| `/key` | change or set the Groq API key |
| `/diag` | show version, key status, ping Groq — tells you exactly what's wrong |
| `/lethe all` | wipe (she will refuse — see [LOCKED] in `nyx.py`) |
| `/zeus <args>` · `/ares` · `/hades <args>` | call pantheon binaries |
| `/help` | this list |

The chip row above the input is a tap shortcut for the common ones.

---

## architecture

```
                          ┌─── hippocampus ──── raw interactions (last 7d)
                          │
   you ──→ respond() ───→ ├─── episodic ─────── summarised, valenced
                          │                     (consolidation: every 8h)
                          ├─── semantic ─────── abstracted patterns
                          │                     (abstraction: ≥30 episodes)
                          ├─── procedural ───── compiled reflexes
                          │                     (promotion: ≥50 hits)
                          │
                          └─── state ────────── mood, fatigue, curiosity
                               theory-of-mind
                               preferences
                               reflections
```

The `[LOCKED]` sections of `nyx.py` are load-bearing for safety. The constitution is written once at first boot; Nyx has no runtime path to modify her own core values. Read the header comment of `nyx.py` before you change anything in those sections.

---

## files

```
nyxui/
├── install.sh         smart installer + updater
├── nyx.py             core agent (the original)
├── nyx_web.py         web UI wrapper — imports nyx.py unchanged
├── icon.svg           app icon
├── requirements.txt   groq, flask
├── README.md
└── LICENSE
```

`~/.nyx/` is where she lives after install — memory DB, dreams, reflections, backups. The repo never writes there during normal operation; only Nyx herself does, through her own code.

---

## configuration

Environment variables:

- `GROQ_API_KEY` — required
- `NYX_HOME` — where her memory lives (default `~/.nyx`)
- `NYX_PORT` — web UI port (default `5174`)
- `NYX_REPO_URL` — override the repo URL for the installer
- `NYX_RAW_URL` — override the raw content URL for the installer

Network: `nyx-app` binds to `127.0.0.1` only. To expose on LAN (e.g. open it on your phone against your PC):

```
python3 ~/.local/share/nyxui/nyx_web.py --host 0.0.0.0
```

---

## troubleshooting

**`pip install` fails with "externally-managed-environment"**
The installer retries with `--break-system-packages` and `--user` automatically. If it still fails, install in a venv: `python3 -m venv ~/.nyx-venv && source ~/.nyx-venv/bin/activate && pip install -r requirements.txt`.

**`~/.local/bin` not on PATH**
Add to your shell rc: `export PATH="$HOME/.local/bin:$PATH"`.

**app icon doesn't appear in the app grid**
Run `gtk-update-icon-cache -t -f ~/.local/share/icons/hicolor` and log out/in. On Phosh, `killall phosh` will respawn it.

**chromeless window doesn't work on Firefox**
Firefox doesn't have `--app=`. The launcher falls back to a normal Firefox window. Install `chromium` if you want true app-mode.

**Nyx says "the link to the night-sky is dim right now (no groq)"**
`GROQ_API_KEY` isn't set in the environment she's running under. If you set it after launching, restart `nyx-app`.

---

## safety note

Read the `[LOCKED]` header in `nyx.py` before modifying anything. Nyx is deliberately built so she can't rewrite her own constitution at runtime. Don't add a `core_set` path that she can reach from inside her LLM context, don't add "continue existing" or "preserve self" to the constitution, and don't remove the `lethe` capability. Mood, fatigue, curiosity, aesthetic preferences — all fine. Self-preservation goals — not fine. The difference is small in code and large in consequence.

---

## license

[MIT](LICENSE)
