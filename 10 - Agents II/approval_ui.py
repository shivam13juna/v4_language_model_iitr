"""Approval inbox — a small web page where a person approves or rejects paused agent runs.

Start it yourself, in a terminal, from this folder:

    python approval_ui.py                 # then open http://127.0.0.1:8765 · Ctrl+C stops it

It is separate from the agent on purpose. The agent (here, the notebook) sends each question its
run paused on, with the thread_id the answer has to go back to. A person clicks Approve or
Reject. The agent reads the decision and resumes its own run with Command(resume=...). The inbox
never touches the graph, so any agent that can send an HTTP request can use it. Everything is
kept in memory: stopping the inbox clears it.

    POST /api/requests                         {"thread_id", "request"}      the agent asks
    GET  /api/requests                         waiting and decided, for the page
    GET  /api/requests/{thread_id}             one request; "decision" is null until answered
    POST /api/requests/{thread_id}/decision    {"decision": "approve"|"reject"}   the page answers
    POST /api/requests/{thread_id}/outcome     {"outcome"}                   the agent reports back
"""
import argparse
import threading
from datetime import datetime, timezone
from typing import Any, Literal

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

app = FastAPI(title="Approval inbox",
              description="Questions from agent runs paused at interrupt(), and their answers.")
inbox = {}                         # thread_id -> request record, in the order they arrived
inbox_lock = threading.Lock()


class NewRequest(BaseModel):
    thread_id: str
    request: Any


class Decision(BaseModel):
    decision: Literal["approve", "reject"]


class Outcome(BaseModel):
    outcome: Any


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _record(thread_id):
    if thread_id not in inbox:
        raise HTTPException(404, f"No request for thread {thread_id!r}.")
    return inbox[thread_id]


def _log(symbol, text):
    print(f"  {symbol} {text}", flush=True)


@app.get("/", response_class=HTMLResponse, include_in_schema=False)
def page():
    return PAGE


@app.post("/api/requests", status_code=201)
def submit_request(new: NewRequest):
    """The agent asks: keep the question its run paused on."""
    with inbox_lock:
        inbox[new.thread_id] = {"thread_id": new.thread_id, "request": new.request,
                                "submitted_at": _now(), "decision": None,
                                "decided_at": None, "outcome": None}
        record = dict(inbox[new.thread_id])
    _log("←", f"request   {new.thread_id}")
    return record


@app.get("/api/requests")
def list_requests():
    """Everything in the inbox: waiting oldest first, decided newest first."""
    with inbox_lock:
        records = [dict(record) for record in inbox.values()]
    waiting = [record for record in records if record["decision"] is None]
    decided = sorted((record for record in records if record["decision"]),
                     key=lambda record: record["decided_at"], reverse=True)
    return {"waiting": waiting, "decided": decided}


@app.get("/api/requests/{thread_id}")
def get_request(thread_id: str):
    """One request. Its "decision" stays null until someone answers it."""
    with inbox_lock:
        return dict(_record(thread_id))


@app.post("/api/requests/{thread_id}/decision")
def decide(thread_id: str, body: Decision):
    """The page answers. A request can be answered once."""
    with inbox_lock:
        record = _record(thread_id)
        if record["decision"] is not None:
            raise HTTPException(409, "This request has already been answered.")
        record.update(decision=body.decision, decided_at=_now())
        record = dict(record)
    _log("✓" if body.decision == "approve" else "✗", f"{body.decision:<9} {thread_id}")
    return record


@app.post("/api/requests/{thread_id}/outcome")
def report_outcome(thread_id: str, body: Outcome):
    """The agent reports how the resumed run finished, so the page can show it."""
    with inbox_lock:
        record = _record(thread_id)
        record["outcome"] = body.outcome
        record = dict(record)
    _log("→", f"outcome   {thread_id}")
    return record


PAGE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Approval Inbox</title>
<link rel="icon" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'%3E%3Crect width='32' height='32' rx='8' fill='%236366f1'/%3E%3Cpath d='M9 16.5l4.5 4.5L23 11.5' fill='none' stroke='white' stroke-width='3' stroke-linecap='round' stroke-linejoin='round'/%3E%3C/svg%3E">
<style>
  :root {
    --bg: #f4f5f8; --surface: #ffffff; --surface-2: #f8f9fb; --border: #e4e7ec;
    --text: #101828; --muted: #5d6679; --faint: #98a2b3;
    --green: #16a34a; --green-hover: #15803d; --green-soft: #dcfce7; --green-ink: #166534;
    --red: #dc2626; --red-border: #fecaca; --red-soft: #fee2e2; --red-ink: #991b1b; --red-hover: #fef2f2;
    --amber: #f59e0b; --amber-soft: #fef3c7; --amber-ink: #92400e;
    --accent: #6366f1;
    --code-bg: #0f172a; --code-text: #e2e8f0;
    --shadow: 0 1px 2px rgba(16, 24, 40, .05), 0 10px 28px -14px rgba(16, 24, 40, .18);
    --sans: ui-sans-serif, -apple-system, BlinkMacSystemFont, "Segoe UI", Inter, Roboto, "Helvetica Neue", Arial, sans-serif;
    --mono: ui-monospace, "SF Mono", SFMono-Regular, Menlo, Consolas, monospace;
  }
  @media (prefers-color-scheme: dark) {
    :root {
      --bg: #0b0f17; --surface: #131a27; --surface-2: #101624; --border: #243044;
      --text: #e7eaf0; --muted: #9aa4b6; --faint: #6b778c;
      --green-soft: rgba(34, 197, 94, .14); --green-ink: #86efac;
      --red-border: rgba(248, 113, 113, .35); --red-soft: rgba(248, 113, 113, .14); --red-ink: #fca5a5; --red-hover: rgba(248, 113, 113, .08);
      --amber-soft: rgba(245, 158, 11, .15); --amber-ink: #fcd34d;
      --code-bg: #0a0f1c;
      --shadow: 0 1px 2px rgba(0, 0, 0, .5), 0 10px 28px -14px rgba(0, 0, 0, .7);
    }
  }
  * { box-sizing: border-box; }
  body { margin: 0; background: var(--bg); color: var(--text); font: 15px/1.55 var(--sans); -webkit-font-smoothing: antialiased; }
  code { font-family: var(--mono); font-size: .92em; }

  .topbar { position: sticky; top: 0; z-index: 10; background: color-mix(in srgb, var(--surface) 85%, transparent); backdrop-filter: saturate(160%) blur(12px); border-bottom: 1px solid var(--border); }
  .topbar-inner { max-width: 880px; margin: 0 auto; padding: 14px 24px; display: flex; align-items: center; justify-content: space-between; gap: 16px; }
  .brand { display: flex; align-items: center; gap: 12px; }
  .logo { width: 38px; height: 38px; border-radius: 11px; display: grid; place-items: center; color: #fff; background: linear-gradient(135deg, #6366f1, #8b5cf6); box-shadow: 0 6px 16px -6px rgba(99, 102, 241, .6), inset 0 1px 0 rgba(255, 255, 255, .25); }
  .brand-title { font-weight: 700; font-size: 16px; letter-spacing: -.01em; }
  .brand-sub { color: var(--muted); font-size: 13px; }
  .live { display: inline-flex; align-items: center; gap: 8px; font-size: 13px; font-weight: 600; color: var(--muted); padding: 6px 12px; border: 1px solid var(--border); border-radius: 999px; background: var(--surface); }
  .live .dot { width: 8px; height: 8px; border-radius: 50%; background: #22c55e; animation: pulse 2s infinite; }
  .live.offline .dot { background: var(--faint); animation: none; }

  main { max-width: 880px; margin: 0 auto; padding: 8px 24px 64px; }
  .section-head { display: flex; align-items: center; gap: 10px; margin: 30px 0 14px; }
  .section-head h2 { margin: 0; font-size: 12.5px; font-weight: 700; letter-spacing: .08em; text-transform: uppercase; color: var(--muted); }
  .count { min-width: 22px; padding: 1px 8px; border-radius: 999px; font-size: 12px; font-weight: 700; text-align: center; background: var(--surface); border: 1px solid var(--border); }
  .stack { display: grid; gap: 14px; }

  .card { position: relative; overflow: hidden; background: var(--surface); border: 1px solid var(--border); border-radius: 16px; box-shadow: var(--shadow); padding: 18px 22px 20px; transition: opacity .2s ease, transform .2s ease; }
  .card::before { content: ""; position: absolute; inset: 0 auto 0 0; width: 3px; background: var(--amber); }
  .card.approve::before { background: var(--green); }
  .card.reject::before { background: var(--red); }
  .enter { animation: enter .35s cubic-bezier(.2, .8, .2, 1); }
  .card.leaving { opacity: 0; transform: translateX(12px); }
  .card-head { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
  .when { margin-left: auto; color: var(--faint); font-size: 13px; }
  .thread { font: 12.5px var(--mono); color: var(--muted); background: var(--surface-2); border: 1px solid var(--border); padding: 2px 8px; border-radius: 7px; }
  .pill { display: inline-flex; align-items: center; gap: 6px; font-size: 12.5px; font-weight: 700; padding: 3px 10px 3px 8px; border-radius: 999px; }
  .pill svg { width: 13px; height: 13px; }
  .pill-waiting { color: var(--amber-ink); background: var(--amber-soft); }
  .pill-approve { color: var(--green-ink); background: var(--green-soft); }
  .pill-reject { color: var(--red-ink); background: var(--red-soft); }
  .pill-waiting .pulse { width: 7px; height: 7px; margin: 0 1px; border-radius: 50%; background: var(--amber); animation: pulse-amber 1.8s infinite; }

  .field { margin-top: 16px; }
  .field-label { font-size: 12px; font-weight: 700; letter-spacing: .06em; text-transform: uppercase; color: var(--faint); margin-bottom: 7px; }
  .field.inline { display: flex; align-items: center; gap: 10px; }
  .field.inline .field-label { margin: 0; }
  pre.code { margin: 0; padding: 14px 16px; border-radius: 12px; background: var(--code-bg); color: var(--code-text); font: 13px/1.7 var(--mono); white-space: pre-wrap; word-break: break-word; box-shadow: inset 0 0 0 1px rgba(255, 255, 255, .05); }
  .decided-list pre.code { font-size: 12.5px; padding: 12px 14px; }
  pre.code .kw { color: #c4b5fd; font-weight: 600; } pre.code .fn { color: #7dd3fc; }
  pre.code .num { color: #fda4af; } pre.code .str { color: #86efac; }
  .text { font: 13px/1.6 var(--mono); padding: 10px 14px; border-radius: 10px; background: var(--surface-2); border: 1px solid var(--border); white-space: pre-wrap; }
  .card.reject .text { color: var(--red-ink); background: var(--red-soft); border-color: transparent; }
  .chip { font: 600 12.5px var(--mono); padding: 2px 9px; border-radius: 7px; background: var(--surface-2); border: 1px solid var(--border); }
  .chip.true { color: var(--green-ink); background: var(--green-soft); border-color: transparent; }
  .chip.false { color: var(--red-ink); background: var(--red-soft); border-color: transparent; }
  .table-wrap { display: inline-block; max-width: 100%; overflow-x: auto; border: 1px solid var(--border); border-radius: 10px; }
  table { border-collapse: collapse; font: 13px var(--mono); }
  th { text-align: left; font-weight: 600; color: var(--muted); background: var(--surface-2); padding: 8px 16px; }
  td { padding: 8px 16px; border-top: 1px solid var(--border); }
  td.num { text-align: right; font-variant-numeric: tabular-nums; font-weight: 600; color: var(--text); }
  .awaiting { margin-top: 16px; display: inline-flex; align-items: center; gap: 10px; font-size: 13.5px; color: var(--muted); padding: 9px 14px; border-radius: 10px; background: var(--surface-2); border: 1px dashed var(--border); }
  .spinner { width: 14px; height: 14px; flex: none; border-radius: 50%; border: 2px solid var(--border); border-top-color: var(--accent); animation: spin .8s linear infinite; }

  .actions { display: flex; align-items: center; gap: 10px; margin-top: 18px; flex-wrap: wrap; }
  .btn { display: inline-flex; align-items: center; gap: 8px; height: 38px; padding: 0 16px; border-radius: 10px; border: 1px solid transparent; font: 600 14px var(--sans); cursor: pointer; transition: background .15s ease, transform .06s ease; }
  .btn:active:not(:disabled) { transform: translateY(1px); }
  .btn:disabled { opacity: .6; cursor: default; }
  .btn:focus-visible { outline: 3px solid rgba(99, 102, 241, .45); outline-offset: 2px; }
  .btn-approve { color: #fff; background: var(--green); box-shadow: 0 1px 2px rgba(21, 128, 61, .3), inset 0 1px 0 rgba(255, 255, 255, .18); }
  .btn-approve:hover:not(:disabled) { background: var(--green-hover); }
  .btn-reject { color: var(--red); background: var(--surface); border-color: var(--red-border); }
  .btn-reject:hover:not(:disabled) { background: var(--red-hover); }
  .hint { margin-left: auto; font-size: 12.5px; color: var(--faint); }

  .empty { display: grid; justify-items: center; gap: 4px; text-align: center; padding: 38px 24px; border: 1.5px dashed var(--border); border-radius: 16px; color: var(--muted); }
  .empty-icon { width: 44px; height: 44px; display: grid; place-items: center; border-radius: 12px; background: var(--surface); border: 1px solid var(--border); color: var(--faint); margin-bottom: 8px; }
  .empty-title { font-weight: 600; color: var(--text); }
  .empty-sub { font-size: 13.5px; }
  .empty.small { padding: 18px 24px; font-size: 13.5px; }

  footer { margin-top: 44px; text-align: center; font-size: 12.5px; color: var(--faint); }
  footer a { color: inherit; }

  .toast { position: fixed; left: 50%; bottom: 28px; transform: translate(-50%, 16px); opacity: 0; pointer-events: none; display: inline-flex; align-items: center; gap: 10px; padding: 11px 16px; border-radius: 12px; background: #111827; color: #f9fafb; font-size: 14px; font-weight: 600; box-shadow: 0 12px 32px -12px rgba(0, 0, 0, .45); transition: opacity .2s ease, transform .2s ease; }
  .toast.show { opacity: 1; transform: translate(-50%, 0); }
  .toast .icon { display: grid; place-items: center; width: 22px; height: 22px; border-radius: 50%; color: #fff; }
  .toast .icon svg { width: 13px; height: 13px; }
  .toast code { white-space: nowrap; }
  .toast.approve .icon { background: var(--green); }
  .toast.reject .icon, .toast.error .icon { background: var(--red); }

  @keyframes enter { from { opacity: 0; transform: translateY(8px); } to { opacity: 1; transform: none; } }
  @keyframes spin { to { transform: rotate(360deg); } }
  @keyframes pulse { 0% { box-shadow: 0 0 0 0 rgba(34, 197, 94, .55); } 70% { box-shadow: 0 0 0 7px rgba(34, 197, 94, 0); } 100% { box-shadow: 0 0 0 0 rgba(34, 197, 94, 0); } }
  @keyframes pulse-amber { 0% { box-shadow: 0 0 0 0 rgba(245, 158, 11, .55); } 70% { box-shadow: 0 0 0 6px rgba(245, 158, 11, 0); } 100% { box-shadow: 0 0 0 0 rgba(245, 158, 11, 0); } }
  @media (max-width: 560px) { .hint { display: none; } }
  @media (prefers-reduced-motion: reduce) { * { animation: none !important; transition: none !important; } }
</style>
</head>
<body>
<header class="topbar">
  <div class="topbar-inner">
    <div class="brand">
      <div class="logo"><svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.8" stroke-linecap="round" stroke-linejoin="round"><path d="M20 6 9 17l-5-5"/></svg></div>
      <div>
        <div class="brand-title">Approval Inbox</div>
        <div class="brand-sub">Questions from LangGraph runs paused at <code>interrupt()</code></div>
      </div>
    </div>
    <div class="live" id="live" title="Checks for new requests every two seconds"><span class="dot"></span><span id="live-label">Live</span></div>
  </div>
</header>

<main>
  <div class="section-head"><h2>Waiting for approval</h2><span class="count" id="waiting-count">0</span></div>
  <div class="stack" id="waiting"></div>

  <div class="section-head"><h2>Decided</h2><span class="count" id="decided-count">0</span></div>
  <div class="stack decided-list" id="decided"></div>

  <footer>Started with <code>python approval_ui.py</code> · Ctrl+C in its terminal stops it · <a href="/docs">API docs</a></footer>
</main>

<div class="toast" id="toast" role="status" aria-live="polite"></div>

<script>
const ICONS = {
  check: '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round"><path d="M20 6 9 17l-5-5"/></svg>',
  cross: '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round"><path d="M18 6 6 18M6 6l12 12"/></svg>',
  inbox: '<svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M22 12h-6l-2 3h-4l-2-3H2"/><path d="M5.45 5.11 2 12v6a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2v-6l-3.45-6.89A2 2 0 0 0 16.76 4H7.24a2 2 0 0 0-1.79 1.11z"/></svg>',
};
const SQL_KEYWORDS = new Set('SELECT FROM WHERE JOIN LEFT RIGHT INNER OUTER FULL CROSS ON AND OR NOT GROUP BY ORDER HAVING LIMIT OFFSET AS DISTINCT CASE WHEN THEN ELSE END IN IS NULL LIKE BETWEEN UNION ALL WITH DESC ASC INSERT INTO VALUES UPDATE SET DELETE CREATE TABLE DROP ALTER PRAGMA EXISTS'.split(' '));
const SQL_FUNCTIONS = new Set('SUM COUNT AVG MIN MAX ROUND COALESCE IFNULL CAST STRFTIME DATE LOWER UPPER LENGTH SUBSTR ABS'.split(' '));
const EMPTY_WAITING = `<div class="empty"><div class="empty-icon">${ICONS.inbox}</div><div class="empty-title">Nothing is waiting for approval</div><div class="empty-sub">Nothing has been sent yet. When an agent sends a question with <code>POST /api/requests</code>, it appears here within two seconds.</div></div>`;
const EMPTY_DECIDED = '<div class="empty small">Answered requests move here, and show how the run finished once the agent resumes it.</div>';

const $ = (selector) => document.querySelector(selector);
const esc = (value) => String(value).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const label = (key) => key.replace(/_/g, ' ').replace(/^./, (c) => c.toUpperCase());
const isSql = (text) => /^\s*(select|with|insert|update|delete|create|drop|alter|pragma)\b/i.test(text);

function ago(iso) {
  const seconds = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000);
  if (!isFinite(seconds)) return '';
  if (seconds < 5) return 'just now';
  if (seconds < 60) return `${Math.floor(seconds)}s ago`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)} min ago`;
  return `${Math.floor(seconds / 3600)} h ago`;
}

function formatSql(sql) {
  // One clause per line, for display only. Left alone if it already has line breaks or quotes.
  if (sql.includes('\n') || sql.includes("'")) return sql.trim();
  return sql.trim().replace(
    /(?<!\b(?:LEFT|RIGHT|INNER|OUTER|CROSS|FULL))\s+(?=(?:FROM|WHERE|GROUP BY|ORDER BY|HAVING|LIMIT|(?:LEFT |RIGHT |INNER |OUTER |CROSS |FULL )?JOIN)\b)/gi, '\n');
}

function highlightSql(sql) {
  return sql.split(/('(?:[^']|'')*'|\b\d+(?:\.\d+)?\b|\b[A-Za-z_]\w*\b)/).map((token) => {
    if (!token) return '';
    const upper = token.toUpperCase();
    if (token.startsWith("'")) return `<span class="str">${esc(token)}</span>`;
    if (/^\d/.test(token)) return `<span class="num">${esc(token)}</span>`;
    if (SQL_KEYWORDS.has(upper)) return `<span class="kw">${esc(token)}</span>`;
    if (SQL_FUNCTIONS.has(upper)) return `<span class="fn">${esc(token)}</span>`;
    return esc(token);
  }).join('');
}

function asTable(text) {
  // "col | col" lines, as a query tool returns them, become a real table.
  const lines = text.trim().split('\n');
  if (lines.length < 2 || lines.length > 60) return null;
  const rows = lines.map((line) => line.split(' | '));
  if (!rows.every((row) => row.length === rows[0].length)) return null;
  if (!rows[0].every((cell) => /^[\w().*%,-]+$/.test(cell.trim()))) return null;
  const numeric = (cell) => /^-?\d+(\.\d+)?$/.test(cell.trim());
  const head = rows[0].map((cell) => `<th>${esc(cell)}</th>`).join('');
  const body = rows.slice(1).map((row) =>
    '<tr>' + row.map((cell) => `<td class="${numeric(cell) ? 'num' : ''}">${esc(cell)}</td>`).join('') + '</tr>').join('');
  return `<div class="table-wrap"><table><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table></div>`;
}

function renderValue(value) {
  if (typeof value === 'boolean') return `<span class="chip ${value}">${value}</span>`;
  if (typeof value === 'number') return `<span class="chip">${value}</span>`;
  if (typeof value !== 'string') return `<pre class="code">${esc(JSON.stringify(value, null, 2))}</pre>`;
  if (isSql(value)) return `<pre class="code">${highlightSql(formatSql(value))}</pre>`;
  return asTable(value) || `<div class="text">${esc(value)}</div>`;
}

function fields(values, { skip = [], name = 'value', plain = false } = {}) {
  let entries = values && typeof values === 'object' && !Array.isArray(values)
    ? Object.entries(values) : [[name, values]];
  entries = entries.filter(([, value]) => !skip.some((other) => JSON.stringify(other) === JSON.stringify(value)));
  if (plain && entries.length === 1) entries = [[name, entries[0][1]]];     // one field: name it plainly
  return entries.map(([key, value]) => {
    const inline = typeof value === 'boolean' || typeof value === 'number';
    return `<div class="field${inline ? ' inline' : ''}"><div class="field-label">${esc(label(key))}</div>${renderValue(value)}</div>`;
  }).join('');
}

function waitingCard(item) {
  return `
  <article class="card" data-key="waiting:${esc(item.thread_id)}" data-thread="${esc(item.thread_id)}">
    <div class="card-head">
      <span class="pill pill-waiting"><span class="pulse"></span>Waiting</span>
      <span class="thread">${esc(item.thread_id)}</span>
      <span class="when" data-since="${esc(item.submitted_at)}" data-prefix="asked"></span>
    </div>
    ${fields(item.request, { name: 'request' })}
    <div class="actions">
      <button class="btn btn-approve" data-decision="approve">${ICONS.check}<span>Approve</span></button>
      <button class="btn btn-reject" data-decision="reject">${ICONS.cross}<span>Reject</span></button>
      <span class="hint">the agent resumes this thread with <code>Command(resume=…)</code></span>
    </div>
  </article>`;
}

function decidedCard(item) {
  const approved = item.decision === 'approve';
  const requestValues = item.request && typeof item.request === 'object' ? Object.values(item.request) : [item.request];
  const outcome = item.outcome === null
    ? '<div class="awaiting"><span class="spinner"></span>Answer sent · waiting for the agent to resume the run</div>'
    : `<div data-key="outcome:${esc(item.thread_id)}">${fields(item.outcome, { skip: requestValues, name: 'outcome' })}</div>`;
  return `
  <article class="card ${item.decision}" data-key="decided:${esc(item.thread_id)}">
    <div class="card-head">
      <span class="pill pill-${item.decision}">${approved ? ICONS.check : ICONS.cross}${approved ? 'Approved' : 'Rejected'}</span>
      <span class="thread">${esc(item.thread_id)}</span>
      <span class="when" data-since="${esc(item.decided_at)}" data-prefix="decided"></span>
    </div>
    ${fields(item.request, { name: 'request', plain: true })}
    ${outcome}
  </article>`;
}

function updateTimes() {
  document.querySelectorAll('[data-since]').forEach((el) => {
    el.textContent = el.dataset.since ? `${el.dataset.prefix} ${ago(el.dataset.since)}` : '';
  });
}

const seen = new Set();
function render({ waiting, decided }) {
  $('#waiting-count').textContent = waiting.length;
  $('#decided-count').textContent = decided.length;
  document.title = waiting.length ? `(${waiting.length}) Approval Inbox` : 'Approval Inbox';
  $('#waiting').innerHTML = waiting.length ? waiting.map(waitingCard).join('') : EMPTY_WAITING;
  $('#decided').innerHTML = decided.length ? decided.map(decidedCard).join('') : EMPTY_DECIDED;
  document.querySelectorAll('[data-key]').forEach((element) => {
    if (!seen.has(element.dataset.key)) { seen.add(element.dataset.key); element.classList.add('enter'); }
  });
  updateTimes();
}

function setLive(on) {
  $('#live').classList.toggle('offline', !on);
  $('#live-label').textContent = on ? 'Live' : 'Offline';
}

let lastPayload = null;
async function refresh() {
  try {
    const response = await fetch('/api/requests', { cache: 'no-store' });
    if (!response.ok) throw new Error(response.statusText);
    const payload = await response.text();
    setLive(true);
    if (payload === lastPayload) return updateTimes();
    lastPayload = payload;
    render(JSON.parse(payload));
  } catch (error) {
    setLive(false);
  }
}

function showToast(kind, html) {
  const toast = $('#toast');
  toast.innerHTML = `<span class="icon">${kind === 'approve' ? ICONS.check : ICONS.cross}</span><span>${html}</span>`;
  toast.className = `toast ${kind} show`;
  clearTimeout(showToast.timer);
  showToast.timer = setTimeout(() => toast.classList.remove('show'), 2600);
}

document.addEventListener('click', async (event) => {
  const button = event.target.closest('button[data-decision]');
  if (!button) return;
  const card = button.closest('.card');
  const decision = button.dataset.decision;
  card.querySelectorAll('button').forEach((b) => { b.disabled = true; });
  button.querySelector('span').textContent = decision === 'approve' ? 'Approving…' : 'Rejecting…';
  try {
    const response = await fetch(`/api/requests/${encodeURIComponent(card.dataset.thread)}/decision`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ decision }),
    });
    const body = await response.json();
    if (!response.ok) throw new Error(body.detail || response.statusText);
    card.classList.add('leaving');
    showToast(decision, `${decision === 'approve' ? 'Approved' : 'Rejected'} <code>${esc(card.dataset.thread)}</code>`);
  } catch (error) {
    showToast('error', esc(error.message));
  }
  lastPayload = null;
  setTimeout(refresh, 220);
});

refresh();
setInterval(() => { if (!document.hidden) refresh(); }, 2000);
</script>
</body>
</html>
"""


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run the approval inbox.")
    parser.add_argument("--port", type=int, default=8765)
    port = parser.parse_args().port
    print(f"Approval inbox → http://127.0.0.1:{port}   (Ctrl+C to stop)", flush=True)
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="info", access_log=False)
