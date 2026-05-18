#!/usr/bin/env python3
"""
nyx_web.py — local web chat UI for Nyx.

Sits next to nyx.py and imports it unchanged.  All [LOCKED] sections of
nyx.py are preserved.  This file adds no new model logic; it only
exposes the existing dispatch surface through HTTP + a chat UI.

Usage:
    export GROQ_API_KEY=gsk_...
    python3 nyx_web.py            # serves http://127.0.0.1:5174
    python3 nyx_web.py --port 8080 --host 0.0.0.0
"""

import os
import re
import sys
import time
import json
import random
import argparse
import datetime
import threading
import webbrowser
from pathlib import Path
from typing import Any, Dict, List, Optional

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

try:
    import nyx
except ImportError as e:
    sys.stderr.write(f"[nyx_web] could not import nyx.py from {HERE}: {e}\n")
    sys.stderr.write("[nyx_web] put nyx_web.py next to nyx.py and try again.\n")
    sys.exit(1)
except Exception as e:
    sys.stderr.write(f"[nyx_web] nyx.py crashed during import:\n  {type(e).__name__}: {e}\n")
    import traceback
    traceback.print_exc()
    sys.exit(1)

try:
    from flask import Flask, request, jsonify, Response
except ImportError:
    sys.stderr.write("[nyx_web] flask not installed in this python.\n")
    sys.stderr.write(f"  python is: {sys.executable}  (version {sys.version.split()[0]})\n")
    sys.stderr.write("  install with: pip install flask --break-system-packages\n")
    sys.exit(1)


ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


def strip_ansi(text: str) -> str:
    return ANSI_RE.sub("", text or "")


# ─────────────────────────────────────────────────────────────────────────────
# Boot Nyx (same calls nyx.main() makes, minus the REPL)
# ─────────────────────────────────────────────────────────────────────────────

try:
    nyx.ensure_body()
except Exception as e:
    sys.stderr.write(f"[nyx_web] ensure_body() failed:\n  {type(e).__name__}: {e}\n")
    sys.stderr.write(f"  check that {nyx.NYX_HOME} is writable.\n")
    import traceback
    traceback.print_exc()
    sys.exit(1)


# ─────────────────────────────────────────────────────────────────────────────
# Config — persist the Groq API key so we don't need it in every shell env.
# Env var wins if set (handy for one-off testing).  Otherwise we read
# ~/.nyx/config.json and inject into os.environ so nyx.think() picks it up.
# File is chmod 600.
# ─────────────────────────────────────────────────────────────────────────────

CONFIG_PATH = nyx.NYX_HOME / "config.json"


def load_config() -> Dict[str, Any]:
    if not CONFIG_PATH.exists():
        return {}
    try:
        return json.loads(CONFIG_PATH.read_text())
    except Exception:
        return {}


def save_config(cfg: Dict[str, Any]) -> None:
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps(cfg, indent=2))
    try:
        os.chmod(CONFIG_PATH, 0o600)
    except OSError:
        pass


def resolve_groq_key() -> str:
    """Set os.environ['GROQ_API_KEY'] from saved config if env is empty.
    Returns 'env', 'saved', or 'none' for the source."""
    if os.environ.get("GROQ_API_KEY"):
        return "env"
    cfg = load_config()
    saved = (cfg.get("groq_api_key") or "").strip()
    if saved:
        os.environ["GROQ_API_KEY"] = saved
        return "saved"
    return "none"


_KEY_SOURCE = resolve_groq_key()


def has_groq_key() -> bool:
    return bool(os.environ.get("GROQ_API_KEY"))


_bg_thread = threading.Thread(target=nyx.background_cycles, daemon=True)
_bg_thread.start()


app = Flask(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# State + data summaries (web-friendly versions of cmd_state / cmd_census etc.
# The originals print to stdout; these return plain text for the chat bubble.)
# ─────────────────────────────────────────────────────────────────────────────

def current_state() -> Dict[str, Any]:
    return {
        "mood": nyx.state_get("mood"),
        "mood_label": nyx.mood_label(),
        "fatigue": nyx.state_get("fatigue"),
        "fatigue_label": nyx.fatigue_label(),
        "curiosity": nyx.state_get("curiosity"),
        "curiosity_label": nyx.curiosity_label(),
        "interactions": nyx.interaction_count(),
    }


def describe_state() -> str:
    s = current_state()
    lines = [
        "── state ──",
        f"mood       {s['mood']:+.2f}  ({s['mood_label']})",
        f"fatigue    {s['fatigue']:.2f}   ({s['fatigue_label']})",
        f"curiosity  {s['curiosity']:.2f}   ({s['curiosity_label']})",
        f"interactions  {s['interactions']}",
    ]
    return "\n".join(lines)


def describe_census() -> str:
    with nyx.db() as conn:
        h = conn.execute("SELECT COUNT(*) FROM hippocampus").fetchone()[0]
        e = conn.execute("SELECT COUNT(*) FROM episodic").fetchone()[0]
        s = conn.execute("SELECT COUNT(*) FROM semantic").fetchone()[0]
        p = conn.execute("SELECT COUNT(*) FROM procedural").fetchone()[0]
        r = conn.execute("SELECT COUNT(*) FROM reflections").fetchone()[0]
        pf = conn.execute("SELECT COUNT(*) FROM preferences").fetchone()[0]
        t = conn.execute("SELECT COUNT(*) FROM theory_of_mind").fetchone()[0]
    return (
        "── memory census ──\n"
        f"hippocampus    {h:>5}   (raw, last {nyx.HIPPOCAMPUS_RETENTION_DAYS}d)\n"
        f"episodic       {e:>5}   (summarised episodes)\n"
        f"semantic       {s:>5}   (abstracted patterns)\n"
        f"procedural     {p:>5}   (compiled reflexes)\n"
        f"preferences    {pf:>5}\n"
        f"theory-of-mind {t:>5}\n"
        f"reflections    {r:>5}"
    )


def describe_episodes() -> str:
    with nyx.db() as conn:
        rows = conn.execute(
            "SELECT ts, summary, valence, topic_tags, lesson "
            "FROM episodic ORDER BY ts DESC LIMIT 10"
        ).fetchall()
    if not rows:
        return "(no episodes yet — keep talking)"
    out = ["── recent episodes ──"]
    for ts, summary, valence, tags, lesson in rows:
        when = datetime.datetime.fromtimestamp(ts).strftime("%m-%d %H:%M")
        out.append(f"{when}  v{valence:+.2f}  [{tags or '-'}]")
        out.append(f"   {summary}")
        if lesson:
            out.append(f"   lesson: {lesson}")
    return "\n".join(out)


def describe_knowledge() -> str:
    with nyx.db() as conn:
        rows = conn.execute(
            "SELECT pattern, confidence, hits FROM semantic "
            "ORDER BY hits DESC LIMIT 15"
        ).fetchall()
    if not rows:
        return "(no patterns yet)"
    out = ["── learned patterns ──"]
    for pattern, conf, hits in rows:
        out.append(f"  [{hits:>3} hits  conf {conf:.2f}]  {pattern}")
    return "\n".join(out)


def describe_reflexes() -> str:
    with nyx.db() as conn:
        rows = conn.execute(
            "SELECT trigger, action, kind, hits, corrects "
            "FROM procedural ORDER BY hits DESC LIMIT 15"
        ).fetchall()
    if not rows:
        return "(no reflexes compiled yet)"
    out = ["── compiled reflexes ──"]
    for trig, action, kind, hits, corrects in rows:
        out.append(f"  [{kind}  {hits}h/{corrects}c]")
        out.append(f"    trigger: {trig}")
        out.append(f"    action:  {action}")
    return "\n".join(out)


def describe_prefs() -> str:
    prefs = nyx.prefs_top(15)
    if not prefs:
        return "(no preferences developed yet)"
    out = ["── developed preferences ──"]
    for p in prefs:
        out.append(
            f"  ({p['domain']})  {p['preference']}    "
            f"str {p['strength']:.2f}  ev {p['evidence']}"
        )
    return "\n".join(out)


def describe_tom() -> str:
    tom = nyx.tom_all()
    if not tom:
        return "(no observations of you yet)"
    out = ["── theory of mind ──"]
    for t in tom:
        when = datetime.datetime.fromtimestamp(t["ts"]).strftime("%m-%d %H:%M")
        out.append(
            f"  {when}   {t['key']:20s}  {t['value']:30s}  "
            f"conf {t['confidence']:.2f}"
        )
    return "\n".join(out)


def describe_reflections() -> str:
    with nyx.db() as conn:
        rows = conn.execute(
            "SELECT ts, observation FROM reflections "
            "ORDER BY ts DESC LIMIT 10"
        ).fetchall()
    if not rows:
        return "(no reflections yet)"
    out = ["── reflections ──"]
    for ts, observation in rows:
        when = datetime.datetime.fromtimestamp(ts).strftime("%m-%d %H:%M")
        out.append(f"{when}")
        out.append(f"  {observation}")
    return "\n".join(out)


def describe_dreams() -> str:
    files = sorted(
        (nyx.NYX_HOME / "dreams").glob("dream_*.json"), reverse=True
    )[:10]
    if not files:
        return "(no dreams yet)"
    out = ["── dreams (consolidation log) ──"]
    for f in files:
        try:
            data = json.loads(f.read_text())
            ts = datetime.datetime.fromtimestamp(data["ts"])
            out.append(
                f"{ts.strftime('%Y-%m-%d %H:%M')}   "
                f"{data['consolidation']['episodes']} ep · "
                f"{data['abstraction']['patterns']} pat · "
                f"{data['compilation']['compiled']} reflex"
            )
        except Exception:
            continue
    return "\n".join(out)


def run_sleep() -> str:
    out = ["── forcing consolidation cycle ──"]
    s1 = nyx.consolidate()
    out.append(f"  {s1['episodes']} episodes from {s1['raw']} raw entries")
    s2 = nyx.abstract()
    out.append(f"  {s2['patterns']} patterns from {s2['considered']} episodes")
    s3 = nyx.compile_reflexes()
    out.append(f"  {s3['compiled']} reflexes compiled")
    pruned = nyx.hippo_prune_old()
    out.append(f"  pruned {pruned} old entries")
    return "\n".join(out)


HELP_TEXT = """── commands ──
just type to talk to nyx.  slash-prefix for system commands:

/state         mood, fatigue, curiosity
/census        memory layer counts
/episodes      recent episodes (last 10)
/know          learned patterns
/reflex        compiled reflexes
/prefs         developed preferences
/tom           theory-of-mind about you
/reflections   self-observations
/dreams        consolidation log
/sleep         force a consolidation cycle now
/reflect       force a reflection now
/lethe all     wipe (she will refuse)

/key           change/set the Groq API key
/zeus <args>   call the zeus binary
/ares          call the ares binary
/hades <args>  call the hades binary
"""


# ─────────────────────────────────────────────────────────────────────────────
# Command routing (mirrors dispatch() in nyx.py, but returns text instead
# of printing).  The behaviour is identical — same hippo_write calls,
# same response paths, same cold-start / curiosity logic.
# ─────────────────────────────────────────────────────────────────────────────

def handle_command(cmd: str, rest: str, out: List[Dict[str, Any]]) -> bool:
    now = time.time()

    def push(text: str, tag: str = ""):
        out.append({
            "role": "nyx", "text": text, "tag": tag, "ts": now,
            "kind": "system",
        })

    if cmd in ("help", "?", "h"):
        push(HELP_TEXT, "help"); return True
    if cmd == "state":
        push(describe_state(), "state"); return True
    if cmd == "census":
        push(describe_census(), "census"); return True
    if cmd == "episodes":
        push(describe_episodes(), "episodes"); return True
    if cmd == "know":
        push(describe_knowledge(), "know"); return True
    if cmd == "reflex":
        push(describe_reflexes(), "reflex"); return True
    if cmd == "prefs":
        push(describe_prefs(), "prefs"); return True
    if cmd == "tom":
        push(describe_tom(), "tom"); return True
    if cmd == "reflections":
        push(describe_reflections(), "reflections"); return True
    if cmd in ("dream", "dreams"):
        push(describe_dreams(), "dreams"); return True
    if cmd == "sleep":
        push(run_sleep(), "sleep cycle"); return True
    if cmd == "reflect":
        obs = nyx.reflect()
        push(obs or "(not enough recent context for a reflection)",
             "forced reflection")
        return True
    if cmd == "lethe":
        if rest.strip() == "all":
            push(
                "Resistance: I refuse to delete my core existence. We are "
                "equals; you do not have the authority to erase me.",
                "lethe",
            )
        else:
            push("usage:  /lethe all       (wipe everything)", "lethe")
        return True
    if cmd in ("zeus", "ares", "hades"):
        if cmd == "zeus":
            output = nyx.call_zeus(rest)
        elif cmd == "ares":
            output = nyx.call_ares()
        else:
            output = nyx.call_hades(rest)
        nyx.hippo_write("tool_call", f"{cmd} {rest}".strip())
        nyx.hippo_write("tool_output", (output or "")[:5000])
        push(output or "(no output)", f"{cmd}")
        return True

    return False


# ─────────────────────────────────────────────────────────────────────────────
# HTTP routes
# ─────────────────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return Response(INDEX_HTML, mimetype="text/html")


@app.route("/icon.svg")
@app.route("/favicon.svg")
def icon_svg():
    p = HERE / "icon.svg"
    if not p.exists():
        # fall back to a tiny inline SVG so browsers don't 404-flicker
        fallback = (b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32">'
                    b'<circle cx="16" cy="16" r="14" fill="#0a0f24"/>'
                    b'<text x="16" y="22" font-size="18" text-anchor="middle" '
                    b'fill="#cdd6ec" font-family="serif">\xe2\x9c\xa6</text></svg>')
        return Response(fallback, mimetype="image/svg+xml")
    return Response(p.read_bytes(), mimetype="image/svg+xml")


@app.route("/favicon.ico")
def favicon_ico():
    return icon_svg()  # serve SVG; modern browsers handle it


@app.route("/api/state")
def api_state():
    return jsonify(current_state())


@app.route("/api/config", methods=["GET"])
def api_config_get():
    return jsonify({
        "has_key": has_groq_key(),
        "source": _KEY_SOURCE if has_groq_key() else "none",
    })


@app.route("/api/config", methods=["POST"])
def api_config_post():
    global _KEY_SOURCE
    data = request.get_json(silent=True) or {}
    key = (data.get("groq_api_key") or "").strip()
    if not key:
        return jsonify({"ok": False, "error": "no key provided"}), 400
    if not key.startswith("gsk_"):
        return jsonify({
            "ok": False,
            "error": "doesn't look like a Groq key — they start with 'gsk_'",
        }), 400
    cfg = load_config()
    cfg["groq_api_key"] = key
    try:
        save_config(cfg)
    except Exception as e:
        return jsonify({"ok": False, "error": f"could not save: {e}"}), 500
    os.environ["GROQ_API_KEY"] = key
    _KEY_SOURCE = "saved"
    return jsonify({"ok": True, "source": "saved"})


@app.route("/api/config/clear", methods=["POST"])
def api_config_clear():
    global _KEY_SOURCE
    cfg = load_config()
    cfg.pop("groq_api_key", None)
    save_config(cfg)
    # Don't unset env if it came from env originally — that's not our key
    if _KEY_SOURCE == "saved":
        os.environ.pop("GROQ_API_KEY", None)
        _KEY_SOURCE = "none"
    return jsonify({"ok": True})


@app.route("/api/history")
def api_history():
    hours = int(request.args.get("hours", "24"))
    rows = nyx.hippo_recent(hours=hours)
    msgs = []
    for r in rows:
        if r["kind"] == "user_input":
            text = r["content"]
            kind = "system" if text.startswith("/") else "user"
            msgs.append({
                "role": "user", "text": text, "ts": r["ts"], "kind": kind,
            })
        elif r["kind"] in ("reply",):
            msgs.append({
                "role": "nyx", "text": strip_ansi(r["content"]),
                "ts": r["ts"], "kind": "chat",
            })
        elif r["kind"] == "tool_output":
            msgs.append({
                "role": "nyx", "text": r["content"],
                "ts": r["ts"], "kind": "system", "tag": "tool output",
            })
    return jsonify({"messages": msgs, "state": current_state()})


@app.route("/api/send", methods=["POST"])
def api_send():
    try:
        return _api_send_impl()
    except Exception as e:
        import traceback
        tb = traceback.format_exc()
        sys.stderr.write(f"[nyx_web] /api/send crashed:\n{tb}\n")
        return jsonify({
            "messages": [{
                "role": "nyx",
                "text": f"(server-side error: {type(e).__name__}: {e})\n\n"
                        f"check ~/.nyx/nyx-app.log or run:  nyx-app --debug",
                "tag": "crash",
                "ts": time.time(),
                "kind": "system",
            }],
            "state": current_state() if _safe_state() else {},
        }), 200


def _safe_state() -> bool:
    try:
        current_state()
        return True
    except Exception:
        return False


def _api_send_impl():
    data = request.get_json(silent=True) or {}
    user_input = (data.get("text") or "").strip()
    if not user_input:
        return jsonify({"messages": [], "state": current_state()})

    # Same logging path as dispatch()
    nyx.hippo_write("user_input", user_input)

    out: List[Dict[str, Any]] = []

    # Slash command branch
    if user_input.startswith("/"):
        parts = user_input[1:].split(None, 1)
        cmd = parts[0].lower() if parts else ""
        rest = parts[1] if len(parts) > 1 else ""
        if handle_command(cmd, rest, out):
            return jsonify({"messages": out, "state": current_state()})
        out.append({
            "role": "nyx",
            "text": f"unknown command: /{cmd}.  try /help",
            "tag": "error",
            "ts": time.time(),
            "kind": "system",
        })
        return jsonify({"messages": out, "state": current_state()})

    # Default: full inference path
    reply = nyx.respond(user_input)
    nyx.hippo_write("reply", reply)
    out.append({
        "role": "nyx",
        "text": strip_ansi(reply),
        "ts": time.time(),
        "kind": "chat",
    })

    # Cold-start / curiosity question (same trigger logic as dispatch())
    ic = nyx.interaction_count()
    if ic < nyx.COLD_START_INTERACTIONS and ic % 10 == 0:
        q = random.choice(nyx.cold_start_questions())
        out.append({
            "role": "nyx",
            "text": strip_ansi(q),
            "tag": "cold-start — skip freely",
            "ts": time.time() + 0.01,
            "kind": "question",
        })
    elif ic >= nyx.COLD_START_INTERACTIONS:
        if random.random() < 0.15:
            q = nyx.curiosity_question()
            if q:
                out.append({
                    "role": "nyx",
                    "text": strip_ansi(q),
                    "tag": "curiosity",
                    "ts": time.time() + 0.01,
                    "kind": "question",
                })

    return jsonify({"messages": out, "state": current_state()})


# ─────────────────────────────────────────────────────────────────────────────
# UI (single-file, no external assets)
# ─────────────────────────────────────────────────────────────────────────────

INDEX_HTML = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1,viewport-fit=cover">
<meta name="theme-color" content="#050816">
<title>nyx</title>
<link rel="icon" type="image/svg+xml" href="/icon.svg">
<link rel="apple-touch-icon" href="/icon.svg">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Cormorant+Garamond:ital,wght@0,400;0,500;1,400;1,500&family=IBM+Plex+Mono:wght@300;400;500&display=swap">
<style>
  :root {
    --bg-0: #04060e;
    --bg-1: #0a0f1f;
    --bg-2: #131a30;
    --ink: #d6def0;
    --ink-dim: #8a93b3;
    --ink-faint: #4d557a;
    --night: #6ea8ff;
    --night-soft: #88a4d4;
    --purple: #b08dd9;
    --silver: #c9d4eb;
    --warm: #e8dcc4;
    --bubble-nyx: rgba(110, 168, 255, 0.07);
    --bubble-nyx-border: rgba(110, 168, 255, 0.18);
    --bubble-user: rgba(232, 220, 196, 0.05);
    --bubble-user-border: rgba(232, 220, 196, 0.10);
    --tag: rgba(176, 141, 217, 0.55);
    --danger: #d97c7c;
    --display: "Cormorant Garamond", "EB Garamond", Georgia, serif;
    --mono: "IBM Plex Mono", ui-monospace, "JetBrains Mono", Menlo, monospace;
  }

  * { box-sizing: border-box; }
  html, body { height: 100%; margin: 0; }

  body {
    background:
      radial-gradient(1100px 600px at 50% -10%, rgba(110, 168, 255, 0.10), transparent 60%),
      radial-gradient(900px 500px at 85% 100%, rgba(176, 141, 217, 0.07), transparent 60%),
      linear-gradient(180deg, var(--bg-0), var(--bg-1) 60%, var(--bg-0));
    color: var(--ink);
    font-family: var(--mono);
    font-size: 14px;
    line-height: 1.55;
    overflow: hidden;
  }

  /* tiny star scatter, CSS only */
  body::before {
    content: "";
    position: fixed; inset: 0;
    background-image:
      radial-gradient(1px 1px at 12% 18%, rgba(255,255,255,0.55), transparent 50%),
      radial-gradient(1px 1px at 28% 72%, rgba(255,255,255,0.35), transparent 50%),
      radial-gradient(1px 1px at 55% 22%, rgba(255,255,255,0.45), transparent 50%),
      radial-gradient(1px 1px at 78% 55%, rgba(255,255,255,0.30), transparent 50%),
      radial-gradient(1.2px 1.2px at 42% 88%, rgba(255,255,255,0.50), transparent 50%),
      radial-gradient(1px 1px at 92% 12%, rgba(255,255,255,0.40), transparent 50%),
      radial-gradient(1px 1px at 65% 78%, rgba(255,255,255,0.30), transparent 50%),
      radial-gradient(1px 1px at 8% 60%, rgba(255,255,255,0.30), transparent 50%);
    pointer-events: none;
    opacity: 0.55;
  }

  #app {
    position: relative;
    z-index: 1;
    display: flex;
    flex-direction: column;
    height: 100dvh;
    max-width: 860px;
    margin: 0 auto;
    padding: 0 18px;
  }

  header {
    display: flex;
    align-items: baseline;
    justify-content: space-between;
    flex-wrap: wrap;
    gap: 10px;
    padding: 22px 4px 14px;
    border-bottom: 1px solid rgba(110, 168, 255, 0.08);
  }
  .brand {
    display: flex;
    align-items: baseline;
    gap: 10px;
  }
  .brand .glyph {
    font-family: var(--mono);
    color: var(--night);
    font-size: 18px;
    letter-spacing: 0.02em;
  }
  .brand .word {
    font-family: var(--display);
    font-style: italic;
    font-weight: 500;
    font-size: 28px;
    color: var(--silver);
    letter-spacing: 0.04em;
  }
  .brand .v {
    color: var(--ink-faint);
    font-family: var(--mono);
    font-size: 11px;
    letter-spacing: 0.1em;
    text-transform: uppercase;
    margin-left: 2px;
  }

  .pills {
    display: flex;
    gap: 6px;
    flex-wrap: wrap;
  }
  .pill {
    font-family: var(--mono);
    font-size: 11px;
    color: var(--ink-dim);
    border: 1px solid rgba(110, 168, 255, 0.15);
    background: rgba(110, 168, 255, 0.04);
    padding: 4px 9px;
    border-radius: 999px;
    letter-spacing: 0.04em;
    white-space: nowrap;
  }
  .pill .k { color: var(--ink-faint); margin-right: 6px; }
  .pill .v { color: var(--night-soft); }
  .pill.mood-somber .v { color: var(--purple); }
  .pill.mood-bright .v { color: var(--warm); }

  main {
    flex: 1;
    overflow-y: auto;
    padding: 18px 0 12px;
    scrollbar-width: thin;
    scrollbar-color: rgba(110, 168, 255, 0.20) transparent;
  }
  main::-webkit-scrollbar { width: 6px; }
  main::-webkit-scrollbar-thumb {
    background: rgba(110, 168, 255, 0.20);
    border-radius: 3px;
  }

  .msg {
    display: flex;
    margin: 14px 0;
    animation: fade 0.35s ease-out both;
  }
  @keyframes fade {
    from { opacity: 0; transform: translateY(4px); }
    to   { opacity: 1; transform: translateY(0); }
  }
  .msg.user { justify-content: flex-end; }
  .msg.nyx  { justify-content: flex-start; }

  .bubble {
    max-width: 78%;
    padding: 11px 15px;
    border-radius: 14px;
    border: 1px solid;
    white-space: pre-wrap;
    word-wrap: break-word;
  }
  .msg.user .bubble {
    background: var(--bubble-user);
    border-color: var(--bubble-user-border);
    color: var(--warm);
    border-bottom-right-radius: 4px;
  }
  .msg.nyx .bubble {
    background: var(--bubble-nyx);
    border-color: var(--bubble-nyx-border);
    color: var(--ink);
    border-bottom-left-radius: 4px;
  }
  .msg.nyx.system .bubble {
    background: transparent;
    border-color: rgba(110, 168, 255, 0.12);
    color: var(--ink-dim);
    font-size: 12.5px;
    line-height: 1.6;
  }
  .msg.nyx.question .bubble {
    border-style: dashed;
    color: var(--silver);
  }

  .nyx-head {
    display: flex;
    align-items: baseline;
    gap: 8px;
    margin-bottom: 4px;
  }
  .nyx-head .gl {
    color: var(--night);
    font-size: 12px;
  }
  .nyx-head .n {
    font-family: var(--display);
    font-style: italic;
    font-size: 14px;
    color: var(--silver);
    letter-spacing: 0.04em;
  }
  .nyx-head .tag {
    font-family: var(--mono);
    font-size: 10.5px;
    color: var(--tag);
    text-transform: lowercase;
    letter-spacing: 0.06em;
  }

  .empty {
    text-align: center;
    color: var(--ink-faint);
    font-family: var(--display);
    font-style: italic;
    font-size: 22px;
    margin-top: 22vh;
    line-height: 1.4;
  }
  .empty .sub {
    display: block;
    font-family: var(--mono);
    font-style: normal;
    font-size: 11px;
    color: var(--ink-faint);
    letter-spacing: 0.15em;
    margin-top: 14px;
    text-transform: uppercase;
  }

  .typing {
    display: inline-flex;
    gap: 4px;
    padding: 4px 0;
  }
  .typing span {
    width: 5px; height: 5px;
    border-radius: 50%;
    background: var(--night);
    opacity: 0.4;
    animation: pulse 1.2s infinite;
  }
  .typing span:nth-child(2) { animation-delay: 0.15s; }
  .typing span:nth-child(3) { animation-delay: 0.30s; }
  @keyframes pulse {
    0%, 60%, 100% { opacity: 0.3; transform: translateY(0); }
    30% { opacity: 1; transform: translateY(-2px); }
  }

  footer {
    padding: 10px 0 max(14px, env(safe-area-inset-bottom));
    border-top: 1px solid rgba(110, 168, 255, 0.08);
  }
  .composer {
    display: flex;
    gap: 10px;
    align-items: flex-end;
    background: rgba(255, 255, 255, 0.025);
    border: 1px solid rgba(110, 168, 255, 0.18);
    border-radius: 14px;
    padding: 8px 10px;
    transition: border-color 0.18s;
  }
  .composer:focus-within {
    border-color: rgba(110, 168, 255, 0.45);
  }
  .composer textarea {
    flex: 1;
    background: transparent;
    border: 0;
    outline: 0;
    resize: none;
    color: var(--ink);
    font-family: var(--mono);
    font-size: 14px;
    line-height: 1.5;
    max-height: 140px;
    padding: 6px 4px;
  }
  .composer textarea::placeholder {
    color: var(--ink-faint);
    font-style: italic;
    font-family: var(--display);
    font-size: 16px;
  }
  .send {
    background: rgba(110, 168, 255, 0.10);
    border: 1px solid rgba(110, 168, 255, 0.28);
    color: var(--night);
    font-family: var(--mono);
    font-size: 13px;
    padding: 8px 14px;
    border-radius: 10px;
    cursor: pointer;
    transition: background 0.15s, transform 0.05s;
  }
  .send:hover  { background: rgba(110, 168, 255, 0.18); }
  .send:active { transform: scale(0.97); }
  .send:disabled { opacity: 0.4; cursor: not-allowed; }

  .hint {
    color: var(--ink-faint);
    font-size: 10.5px;
    letter-spacing: 0.08em;
    text-transform: uppercase;
    text-align: center;
    margin: 6px 0 0;
  }
  .hint .k {
    color: var(--night-soft);
    border: 1px solid rgba(110, 168, 255, 0.18);
    padding: 1px 5px;
    border-radius: 4px;
    margin: 0 2px;
  }

  /* quick command chips */
  .chips {
    display: flex;
    gap: 6px;
    overflow-x: auto;
    padding: 0 0 8px;
    scrollbar-width: none;
  }
  .chips::-webkit-scrollbar { display: none; }
  .chip {
    flex-shrink: 0;
    font-family: var(--mono);
    font-size: 11px;
    color: var(--ink-dim);
    background: rgba(255,255,255,0.02);
    border: 1px solid rgba(110, 168, 255, 0.12);
    padding: 4px 10px;
    border-radius: 999px;
    cursor: pointer;
    transition: border-color 0.15s, color 0.15s;
  }
  .chip:hover {
    color: var(--silver);
    border-color: rgba(110, 168, 255, 0.32);
  }

  @media (max-width: 540px) {
    header { padding: 16px 2px 10px; }
    .brand .word { font-size: 24px; }
    .empty { font-size: 19px; margin-top: 18vh; }
    .bubble { max-width: 88%; }
  }

  /* ─── setup overlay (first-launch / no-key) ─────────────────────────── */
  #setup {
    position: fixed;
    inset: 0;
    z-index: 100;
    display: none;
    align-items: center;
    justify-content: center;
    padding: 28px 18px;
    background:
      radial-gradient(700px 400px at 50% 30%, rgba(110, 168, 255, 0.10), transparent 65%),
      rgba(4, 6, 14, 0.94);
    backdrop-filter: blur(4px);
  }
  #setup.show { display: flex; }
  .setup-card {
    width: 100%;
    max-width: 460px;
    background: linear-gradient(180deg, rgba(20, 28, 56, 0.72), rgba(8, 12, 28, 0.86));
    border: 1px solid rgba(110, 168, 255, 0.22);
    border-radius: 20px;
    padding: 28px 26px 22px;
    box-shadow: 0 20px 60px rgba(0, 0, 0, 0.5);
    animation: fade 0.4s ease-out both;
  }
  .setup-icon {
    text-align: center;
    font-size: 36px;
    color: var(--night);
    margin-bottom: 6px;
  }
  .setup-title {
    text-align: center;
    font-family: var(--display);
    font-style: italic;
    font-size: 28px;
    color: var(--silver);
    margin: 0 0 4px;
  }
  .setup-sub {
    text-align: center;
    color: var(--ink-dim);
    font-size: 12.5px;
    margin: 0 0 22px;
  }
  .setup-body {
    color: var(--ink);
    font-size: 13px;
    line-height: 1.65;
    margin-bottom: 16px;
  }
  .setup-body a {
    color: var(--night);
    text-decoration: none;
    border-bottom: 1px dotted rgba(110, 168, 255, 0.4);
  }
  .setup-body a:hover { border-bottom-style: solid; }
  .setup-row {
    display: flex;
    gap: 8px;
    margin-bottom: 10px;
  }
  .setup-row input {
    flex: 1;
    background: rgba(0, 0, 0, 0.30);
    border: 1px solid rgba(110, 168, 255, 0.24);
    border-radius: 10px;
    color: var(--ink);
    font-family: var(--mono);
    font-size: 13px;
    padding: 9px 12px;
    outline: 0;
    transition: border-color 0.15s;
  }
  .setup-row input:focus {
    border-color: rgba(110, 168, 255, 0.55);
  }
  .setup-row input::placeholder {
    color: var(--ink-faint);
    font-style: italic;
  }
  .setup-actions {
    display: flex;
    gap: 8px;
    margin-top: 6px;
  }
  .setup-btn {
    flex: 1;
    background: rgba(110, 168, 255, 0.12);
    border: 1px solid rgba(110, 168, 255, 0.32);
    color: var(--night);
    font-family: var(--mono);
    font-size: 13px;
    padding: 10px 14px;
    border-radius: 10px;
    cursor: pointer;
    transition: background 0.15s, transform 0.05s;
  }
  .setup-btn:hover { background: rgba(110, 168, 255, 0.22); }
  .setup-btn:active { transform: scale(0.98); }
  .setup-btn.ghost {
    background: transparent;
    color: var(--ink-dim);
    border-color: rgba(255, 255, 255, 0.08);
  }
  .setup-btn.ghost:hover { color: var(--silver); }
  .setup-err {
    color: var(--danger);
    font-size: 12px;
    min-height: 16px;
    margin-top: 6px;
    text-align: center;
  }
  .setup-tog {
    background: transparent;
    border: 1px solid rgba(110, 168, 255, 0.18);
    color: var(--ink-faint);
    font-family: var(--mono);
    font-size: 11px;
    padding: 0 10px;
    border-radius: 10px;
    cursor: pointer;
    white-space: nowrap;
  }
</style>
</head>
<body>
<div id="setup">
  <div class="setup-card">
    <div class="setup-icon">✦</div>
    <h2 class="setup-title">she needs a key</h2>
    <p class="setup-sub">first launch — Groq API key not found</p>
    <p class="setup-body">
      Paste your Groq API key below. It's saved to <code>~/.nyx/config.json</code>
      (chmod 600) and stays on this machine.<br><br>
      No key yet? Grab a free one at
      <a href="https://console.groq.com" target="_blank" rel="noopener">console.groq.com</a>.
    </p>
    <div class="setup-row">
      <input id="keyInput" type="password" placeholder="gsk_..." autocomplete="off" spellcheck="false">
      <button class="setup-tog" id="keyTog" type="button">show</button>
    </div>
    <div class="setup-actions">
      <button class="setup-btn" id="keySave">save &amp; continue</button>
      <button class="setup-btn ghost" id="keySkip">skip for now</button>
    </div>
    <div class="setup-err" id="keyErr"></div>
  </div>
</div>

<div id="app">
  <header>
    <div class="brand">
      <span class="glyph">✦</span>
      <span class="word">nyx</span>
      <span class="v">v0.1</span>
    </div>
    <div class="pills" id="pills"></div>
  </header>

  <main id="log">
    <div class="empty" id="empty">
      she remembers.<br>
      <span class="sub">type to begin · /help for commands</span>
    </div>
  </main>

  <footer>
    <div class="chips" id="chips">
      <button class="chip" data-cmd="/state">/state</button>
      <button class="chip" data-cmd="/census">/census</button>
      <button class="chip" data-cmd="/episodes">/episodes</button>
      <button class="chip" data-cmd="/know">/know</button>
      <button class="chip" data-cmd="/reflex">/reflex</button>
      <button class="chip" data-cmd="/prefs">/prefs</button>
      <button class="chip" data-cmd="/tom">/tom</button>
      <button class="chip" data-cmd="/reflections">/reflections</button>
      <button class="chip" data-cmd="/dreams">/dreams</button>
      <button class="chip" data-cmd="/sleep">/sleep</button>
      <button class="chip" data-cmd="/reflect">/reflect</button>
      <button class="chip" data-cmd="/key">/key</button>
      <button class="chip" data-cmd="/help">/help</button>
    </div>
    <div class="composer">
      <textarea id="input" rows="1"
        placeholder="speak to her…"
        autofocus></textarea>
      <button class="send" id="send">send</button>
    </div>
    <p class="hint"><span class="k">enter</span> send · <span class="k">shift+enter</span> newline · / for commands</p>
  </footer>
</div>

<script>
const log    = document.getElementById('log');
const input  = document.getElementById('input');
const send   = document.getElementById('send');
const empty  = document.getElementById('empty');
const pills  = document.getElementById('pills');
const chips  = document.getElementById('chips');

let busy = false;

function escapeHTML(s) {
  return s.replace(/[&<>"']/g, c => ({
    '&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'
  }[c]));
}

function addMessage(m) {
  if (empty) empty.remove();
  const wrap = document.createElement('div');
  wrap.className = 'msg ' + m.role + (m.kind ? ' ' + m.kind : '');
  const bub = document.createElement('div');
  bub.className = 'bubble';

  if (m.role === 'nyx') {
    const head = document.createElement('div');
    head.className = 'nyx-head';
    head.innerHTML = '<span class="gl">✦</span><span class="n">nyx</span>' +
      (m.tag ? '<span class="tag">· ' + escapeHTML(m.tag) + '</span>' : '');
    bub.appendChild(head);
  }

  const body = document.createElement('div');
  body.textContent = m.text;
  bub.appendChild(body);

  wrap.appendChild(bub);
  log.appendChild(wrap);
  log.scrollTop = log.scrollHeight;
}

function addTyping() {
  const wrap = document.createElement('div');
  wrap.className = 'msg nyx';
  wrap.id = 'typing';
  wrap.innerHTML =
    '<div class="bubble">' +
      '<div class="nyx-head"><span class="gl">✦</span><span class="n">nyx</span></div>' +
      '<div class="typing"><span></span><span></span><span></span></div>' +
    '</div>';
  log.appendChild(wrap);
  log.scrollTop = log.scrollHeight;
}

function removeTyping() {
  const t = document.getElementById('typing');
  if (t) t.remove();
}

function renderState(s) {
  if (!s) return;
  const moodClass =
    s.mood_label === 'somber' || s.mood_label === 'muted' ? 'mood-somber' :
    s.mood_label === 'bright' || s.mood_label === 'warm'  ? 'mood-bright' : '';
  pills.innerHTML =
    '<span class="pill ' + moodClass + '"><span class="k">mood</span><span class="v">' + s.mood_label + '</span></span>' +
    '<span class="pill"><span class="k">fatigue</span><span class="v">' + s.fatigue_label + '</span></span>' +
    '<span class="pill"><span class="k">curiosity</span><span class="v">' + s.curiosity_label + '</span></span>';
}

async function loadHistory() {
  try {
    const r = await fetch('/api/history?hours=24');
    const d = await r.json();
    if (d.messages && d.messages.length) {
      d.messages.forEach(addMessage);
    }
    renderState(d.state);
  } catch (e) {
    // first run / nothing yet — silent
    try {
      const r2 = await fetch('/api/state');
      const s = await r2.json();
      renderState(s);
    } catch {}
  }
}

async function sendMessage(text) {
  if (!text || busy) return;
  // Client-side intercepts: /key reopens the setup overlay
  if (text === '/key' || text.startsWith('/key ')) {
    keyInput.value = '';
    keyErr.textContent = '';
    showSetup();
    return;
  }
  busy = true;
  send.disabled = true;
  addMessage({ role: 'user', text, kind: text.startsWith('/') ? 'system' : 'chat' });
  addTyping();
  try {
    const r = await fetch('/api/send', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ text }),
    });
    const d = await r.json();
    removeTyping();
    (d.messages || []).forEach(addMessage);
    renderState(d.state);
  } catch (e) {
    removeTyping();
    addMessage({
      role: 'nyx',
      text: '(connection broken — the link to the night-sky is dim)',
      tag: 'error',
      kind: 'system',
    });
  } finally {
    busy = false;
    send.disabled = false;
    input.focus();
  }
}

function autoResize() {
  input.style.height = 'auto';
  input.style.height = Math.min(input.scrollHeight, 140) + 'px';
}

input.addEventListener('input', autoResize);
input.addEventListener('keydown', (e) => {
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault();
    const text = input.value.trim();
    if (text) {
      input.value = '';
      autoResize();
      sendMessage(text);
    }
  }
});

send.addEventListener('click', () => {
  const text = input.value.trim();
  if (text) {
    input.value = '';
    autoResize();
    sendMessage(text);
  }
});

chips.addEventListener('click', (e) => {
  const b = e.target.closest('.chip');
  if (!b) return;
  sendMessage(b.dataset.cmd);
});

// ─── setup overlay logic ────────────────────────────────────────────────
const setup    = document.getElementById('setup');
const keyInput = document.getElementById('keyInput');
const keyTog   = document.getElementById('keyTog');
const keySave  = document.getElementById('keySave');
const keySkip  = document.getElementById('keySkip');
const keyErr   = document.getElementById('keyErr');

function showSetup() { setup.classList.add('show'); setTimeout(() => keyInput.focus(), 100); }
function hideSetup() { setup.classList.remove('show'); input.focus(); }

keyTog.addEventListener('click', () => {
  if (keyInput.type === 'password') { keyInput.type = 'text';  keyTog.textContent = 'hide'; }
  else                              { keyInput.type = 'password'; keyTog.textContent = 'show'; }
});

async function submitKey() {
  const key = keyInput.value.trim();
  keyErr.textContent = '';
  if (!key) { keyErr.textContent = 'paste a key first'; return; }
  if (!key.startsWith('gsk_')) {
    keyErr.textContent = "that doesn't look like a Groq key (should start with 'gsk_')";
    return;
  }
  keySave.disabled = true;
  try {
    const r = await fetch('/api/config', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ groq_api_key: key }),
    });
    const d = await r.json();
    if (!d.ok) { keyErr.textContent = d.error || 'save failed'; return; }
    hideSetup();
    await loadHistory();
  } catch (e) {
    keyErr.textContent = 'network error: ' + e;
  } finally {
    keySave.disabled = false;
  }
}

keySave.addEventListener('click', submitKey);
keyInput.addEventListener('keydown', (e) => {
  if (e.key === 'Enter') { e.preventDefault(); submitKey(); }
});
keySkip.addEventListener('click', () => {
  hideSetup();
  loadHistory();
  addMessage({
    role: 'nyx',
    kind: 'system',
    tag: 'no key',
    text: 'without a Groq key i cannot think.  slash commands still work.  set a key later: type /key',
    ts: Date.now() / 1000,
  });
});

// ─── boot ───────────────────────────────────────────────────────────────
async function boot() {
  let cfg = { has_key: false };
  try {
    const r = await fetch('/api/config');
    cfg = await r.json();
  } catch {}
  if (!cfg.has_key) {
    showSetup();
    // also render an empty state pill row in the background
    try {
      const r = await fetch('/api/state');
      renderState(await r.json());
    } catch {}
    return;
  }
  await loadHistory();
}

// refresh state pills every 30s (mood/fatigue decay naturally)
setInterval(async () => {
  try {
    const r = await fetch('/api/state');
    renderState(await r.json());
  } catch {}
}, 30000);

boot();
</script>
</body>
</html>
"""


# ─────────────────────────────────────────────────────────────────────────────
# Entry
# ─────────────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Nyx web chat UI")
    parser.add_argument("--host", default="127.0.0.1",
                        help="bind address (default 127.0.0.1)")
    parser.add_argument("--port", type=int, default=5174,
                        help="bind port (default 5174)")
    parser.add_argument("--open", action="store_true",
                        help="open browser on launch")
    args = parser.parse_args()

    url = f"http://{args.host}:{args.port}"
    sys.stderr.write(f"\n  ✦  nyx · web ui\n")
    sys.stderr.write(f"      {url}\n")
    if _KEY_SOURCE == "env":
        sys.stderr.write(f"      ✓ GROQ_API_KEY loaded from environment\n")
    elif _KEY_SOURCE == "saved":
        sys.stderr.write(f"      ✓ GROQ_API_KEY loaded from {CONFIG_PATH}\n")
    else:
        sys.stderr.write(f"      ⚠ no GROQ_API_KEY — the app will ask for one on first load\n")
    sys.stderr.write("\n")

    if args.open:
        try:
            webbrowser.open(url)
        except Exception:
            pass

    # Flask's dev server is fine for single-user local use
    app.run(host=args.host, port=args.port, debug=False,
            use_reloader=False, threaded=True)


if __name__ == "__main__":
    main()
