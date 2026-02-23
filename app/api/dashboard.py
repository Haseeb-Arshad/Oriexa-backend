"""Dashboard — self-contained HTML page for previewing agent task outputs."""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

router = APIRouter(tags=["dashboard"])

DASHBOARD_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Oriexa Agent Dashboard</title>
<link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.9.0/styles/github-dark.min.css">
<script src="https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.9.0/highlight.min.js"></script>
<script src="https://cdnjs.cloudflare.com/ajax/libs/marked/12.0.0/marked.min.js"></script>
<style>
  :root {
    --bg: #0d1117; --bg2: #161b22; --bg3: #21262d; --border: #30363d;
    --text: #e6edf3; --text2: #8b949e; --accent: #58a6ff; --green: #3fb950;
    --red: #f85149; --yellow: #d29922; --purple: #bc8cff; --orange: #f0883e;
    --teal: #39d353;
  }
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Helvetica, Arial, sans-serif;
         background: var(--bg); color: var(--text); line-height: 1.5; }

  /* Layout */
  .app { display: flex; height: 100vh; }
  .sidebar { width: 320px; min-width: 320px; background: var(--bg2); border-right: 1px solid var(--border);
             display: flex; flex-direction: column; overflow: hidden; }
  .main { flex: 1; display: flex; flex-direction: column; overflow: hidden; }
  .header { padding: 16px 20px; border-bottom: 1px solid var(--border); display: flex;
            align-items: center; gap: 12px; background: var(--bg2); }
  .header h1 { font-size: 18px; font-weight: 600; }
  .header .badge { font-size: 11px; background: var(--accent); color: #000; padding: 2px 8px;
                   border-radius: 10px; font-weight: 600; }
  .content { flex: 1; overflow: auto; padding: 20px; }

  /* Sidebar */
  .sidebar-header { padding: 16px; border-bottom: 1px solid var(--border); }
  .sidebar-header h2 { font-size: 14px; color: var(--text2); text-transform: uppercase;
                       letter-spacing: 0.5px; }
  .exec-list { flex: 1; overflow-y: auto; padding: 8px; }
  .exec-item { padding: 10px 12px; border-radius: 6px; cursor: pointer; margin-bottom: 4px;
               border: 1px solid transparent; transition: all 0.15s ease; }
  .exec-item:hover { background: var(--bg3); border-color: var(--border); }
  .exec-item.active { background: var(--bg3); border-color: var(--accent); }
  .exec-item .title { font-size: 13px; font-weight: 600; white-space: nowrap; overflow: hidden;
                      text-overflow: ellipsis; }
  .exec-item .meta { font-size: 11px; color: var(--text2); display: flex; gap: 8px; margin-top: 3px; }
  .exec-item .status { display: inline-block; padding: 1px 6px; border-radius: 3px; font-size: 10px;
                       font-weight: 600; text-transform: uppercase; }
  .exec-item.live-pulse { border-color: var(--accent); animation: sidebar-pulse 2s ease-in-out infinite; }
  @keyframes sidebar-pulse { 0%,100% { border-color: var(--accent); } 50% { border-color: var(--purple); } }

  .status-completed { background: rgba(63,185,80,0.15); color: var(--green); }
  .status-failed { background: rgba(248,81,73,0.15); color: var(--red); }
  .status-executing, .status-planning, .status-reviewing { background: rgba(88,166,255,0.15); color: var(--accent); }
  .status-pending, .status-claiming { background: rgba(210,153,34,0.15); color: var(--yellow); }

  /* Detail view */
  .detail-header { display: flex; gap: 16px; align-items: flex-start; margin-bottom: 20px; }
  .detail-header .info { flex: 1; }
  .detail-header h2 { font-size: 20px; margin-bottom: 4px; }
  .detail-header .desc { color: var(--text2); font-size: 13px; }
  .stats { display: flex; gap: 16px; flex-wrap: wrap; margin-bottom: 20px; }
  .stat-card { background: var(--bg2); border: 1px solid var(--border); border-radius: 8px;
               padding: 12px 16px; min-width: 120px; }
  .stat-card .label { font-size: 11px; color: var(--text2); text-transform: uppercase; }
  .stat-card .value { font-size: 20px; font-weight: 700; margin-top: 2px; }

  /* Tabs */
  .tabs { display: flex; border-bottom: 1px solid var(--border); margin-bottom: 16px; }
  .tab { padding: 8px 16px; cursor: pointer; font-size: 13px; font-weight: 500;
         border-bottom: 2px solid transparent; color: var(--text2); transition: all 0.15s; }
  .tab:hover { color: var(--text); }
  .tab.active { color: var(--accent); border-bottom-color: var(--accent); }
  .tab.tab-live { position: relative; }
  .tab.tab-live::after { content: ''; position: absolute; top: 6px; right: 4px; width: 6px; height: 6px;
    border-radius: 50%; background: var(--green); animation: live-dot 1.5s ease-in-out infinite; }
  @keyframes live-dot { 0%,100% { opacity: 1; } 50% { opacity: 0.3; } }
  .tab-content { display: none; }
  .tab-content.active { display: block; }

  /* ============================================================ */
  /* PROGRESS PANEL — Shimmer, Thinking, Timeline                 */
  /* ============================================================ */

  /* Overall progress bar */
  .progress-bar-container { margin-bottom: 24px; }
  .progress-bar-label { display: flex; justify-content: space-between; margin-bottom: 6px; font-size: 12px; }
  .progress-bar-label .pct { color: var(--accent); font-weight: 700; font-size: 14px; }
  .progress-bar-label .phase-label { color: var(--text2); }
  .progress-bar-outer { height: 6px; background: var(--bg3); border-radius: 3px; overflow: hidden; position: relative; }
  .progress-bar-inner { height: 100%; border-radius: 3px; transition: width 0.6s cubic-bezier(0.22, 1, 0.36, 1);
    background: linear-gradient(90deg, var(--accent), var(--purple)); position: relative; }
  .progress-bar-inner.active::after { content: ''; position: absolute; top: 0; right: 0; bottom: 0; width: 100px;
    background: linear-gradient(90deg, transparent, rgba(255,255,255,0.15), transparent);
    animation: bar-shimmer 1.5s ease-in-out infinite; }
  @keyframes bar-shimmer { 0% { transform: translateX(-100px); } 100% { transform: translateX(200px); } }

  /* Phase pipeline (horizontal icons) */
  .phase-pipeline { display: flex; align-items: center; justify-content: center; gap: 4px; margin-bottom: 28px;
    padding: 16px 0; }
  .phase-pip { display: flex; flex-direction: column; align-items: center; gap: 6px; min-width: 72px; }
  .phase-pip-icon { width: 36px; height: 36px; border-radius: 50%; display: flex; align-items: center;
    justify-content: center; border: 2px solid var(--border); background: var(--bg2);
    transition: all 0.4s ease; position: relative; }
  .phase-pip-icon svg { width: 16px; height: 16px; stroke: var(--text2); fill: none;
    stroke-width: 2; stroke-linecap: round; stroke-linejoin: round; transition: stroke 0.3s; }
  .phase-pip-label { font-size: 10px; color: var(--text2); text-transform: uppercase;
    letter-spacing: 0.3px; font-weight: 600; transition: color 0.3s; }
  .phase-pip.done .phase-pip-icon { border-color: var(--green); background: rgba(63,185,80,0.1); }
  .phase-pip.done .phase-pip-icon svg { stroke: var(--green); }
  .phase-pip.done .phase-pip-label { color: var(--green); }
  .phase-pip.active .phase-pip-icon { border-color: var(--accent); background: rgba(88,166,255,0.1);
    box-shadow: 0 0 12px rgba(88,166,255,0.3); animation: pip-glow 2s ease-in-out infinite; }
  .phase-pip.active .phase-pip-icon svg { stroke: var(--accent); }
  .phase-pip.active .phase-pip-label { color: var(--accent); }
  @keyframes pip-glow { 0%,100% { box-shadow: 0 0 12px rgba(88,166,255,0.3); }
    50% { box-shadow: 0 0 20px rgba(88,166,255,0.5); } }
  .phase-pip.failed .phase-pip-icon { border-color: var(--red); background: rgba(248,81,73,0.1); }
  .phase-pip.failed .phase-pip-icon svg { stroke: var(--red); }
  .phase-pip.failed .phase-pip-label { color: var(--red); }
  .phase-connector { width: 28px; height: 2px; background: var(--border); margin-bottom: 22px; transition: background 0.4s; }
  .phase-connector.done { background: var(--green); }
  .phase-connector.active { background: var(--accent);
    animation: connector-flow 1s ease-in-out infinite alternate; }
  @keyframes connector-flow { from { opacity: 0.5; } to { opacity: 1; } }

  /* Thinking indicator */
  .thinking-indicator { display: flex; align-items: center; gap: 10px; padding: 14px 18px;
    background: linear-gradient(135deg, rgba(88,166,255,0.06), rgba(188,140,255,0.06));
    border: 1px solid rgba(88,166,255,0.15); border-radius: 10px; margin-bottom: 20px;
    position: relative; overflow: hidden; }
  .thinking-indicator::before { content: ''; position: absolute; top: 0; left: -150%; width: 150%; height: 100%;
    background: linear-gradient(90deg, transparent, rgba(88,166,255,0.08), transparent);
    animation: thinking-sweep 2.5s ease-in-out infinite; }
  @keyframes thinking-sweep { 0% { left: -150%; } 100% { left: 150%; } }
  .thinking-brain { width: 28px; height: 28px; flex-shrink: 0; position: relative; }
  .thinking-brain svg { width: 28px; height: 28px; stroke: var(--purple); fill: none;
    stroke-width: 1.5; animation: brain-pulse 2s ease-in-out infinite; }
  @keyframes brain-pulse { 0%,100% { opacity: 0.7; transform: scale(1); }
    50% { opacity: 1; transform: scale(1.08); } }
  .thinking-text { flex: 1; }
  .thinking-text .thinking-title { font-size: 13px; font-weight: 600; color: var(--accent); margin-bottom: 2px; }
  .thinking-text .thinking-desc { font-size: 12px; color: var(--text2); }
  .thinking-dots { display: inline-flex; gap: 3px; margin-left: 4px; vertical-align: middle; }
  .thinking-dots span { width: 4px; height: 4px; border-radius: 50%; background: var(--accent);
    animation: dot-bounce 1.4s ease-in-out infinite; }
  .thinking-dots span:nth-child(2) { animation-delay: 0.2s; }
  .thinking-dots span:nth-child(3) { animation-delay: 0.4s; }
  @keyframes dot-bounce { 0%,80%,100% { opacity: 0.3; transform: scale(0.8); }
    40% { opacity: 1; transform: scale(1.2); } }

  /* Step timeline */
  .step-timeline { position: relative; padding-left: 24px; }
  .step-timeline::before { content: ''; position: absolute; left: 9px; top: 0; bottom: 0;
    width: 2px; background: var(--border); }
  .step-entry { position: relative; padding-bottom: 20px; animation: step-enter 0.5s ease-out forwards;
    opacity: 0; transform: translateY(8px); }
  @keyframes step-enter { to { opacity: 1; transform: translateY(0); } }
  .step-dot { position: absolute; left: -20px; top: 4px; width: 12px; height: 12px; border-radius: 50%;
    border: 2px solid var(--border); background: var(--bg); z-index: 1; transition: all 0.3s; }
  .step-entry.done .step-dot { border-color: var(--green); background: var(--green); }
  .step-entry.active .step-dot { border-color: var(--accent); background: var(--accent);
    box-shadow: 0 0 8px rgba(88,166,255,0.5); animation: dot-pulse 1.5s ease-in-out infinite; }
  @keyframes dot-pulse { 0%,100% { box-shadow: 0 0 8px rgba(88,166,255,0.3); }
    50% { box-shadow: 0 0 16px rgba(88,166,255,0.6); } }
  .step-entry.failed .step-dot { border-color: var(--red); background: var(--red); }
  .step-card { background: var(--bg2); border: 1px solid var(--border); border-radius: 8px;
    padding: 12px 16px; position: relative; overflow: hidden; transition: border-color 0.3s; }
  .step-entry.active .step-card { border-color: rgba(88,166,255,0.3); }
  .step-card-shimmer { position: absolute; top: 0; left: -150%; width: 150%; height: 100%;
    background: linear-gradient(90deg, transparent, rgba(88,166,255,0.04), rgba(188,140,255,0.04), transparent);
    pointer-events: none; }
  .step-entry.active .step-card-shimmer { animation: card-shimmer 2s ease-in-out infinite; }
  @keyframes card-shimmer { 0% { left: -150%; } 100% { left: 150%; } }
  .step-phase-badge { display: inline-block; font-size: 10px; font-weight: 700; text-transform: uppercase;
    letter-spacing: 0.5px; padding: 2px 8px; border-radius: 3px; margin-bottom: 6px; }
  .badge-triage { background: rgba(210,153,34,0.15); color: var(--yellow); }
  .badge-clarification { background: rgba(240,136,62,0.15); color: var(--orange); }
  .badge-planning { background: rgba(88,166,255,0.15); color: var(--accent); }
  .badge-execution, .badge-complex_execution { background: rgba(188,140,255,0.15); color: var(--purple); }
  .badge-review { background: rgba(57,211,83,0.15); color: var(--teal); }
  .badge-delivery { background: rgba(63,185,80,0.15); color: var(--green); }
  .badge-failed { background: rgba(248,81,73,0.15); color: var(--red); }
  .step-description { font-size: 13px; color: var(--text); margin-bottom: 4px; }
  .step-detail { font-size: 12px; color: var(--text2); line-height: 1.6; }
  .step-time { font-size: 10px; color: var(--text2); margin-top: 6px; opacity: 0.7; }
  .step-meta-chips { display: flex; gap: 6px; flex-wrap: wrap; margin-top: 8px; }
  .step-chip { font-size: 10px; padding: 2px 8px; border-radius: 10px; background: var(--bg3);
    color: var(--text2); border: 1px solid var(--border); }

  /* Skeleton shimmer placeholders */
  .skeleton { background: var(--bg3); border-radius: 4px; position: relative; overflow: hidden; }
  .skeleton::after { content: ''; position: absolute; top: 0; left: -100%; width: 100%; height: 100%;
    background: linear-gradient(90deg, transparent, rgba(255,255,255,0.04), transparent);
    animation: skeleton-sweep 1.5s ease-in-out infinite; }
  @keyframes skeleton-sweep { 0% { left: -100%; } 100% { left: 100%; } }
  .skeleton-line { height: 12px; margin-bottom: 8px; }
  .skeleton-line.w60 { width: 60%; }
  .skeleton-line.w80 { width: 80%; }
  .skeleton-line.w40 { width: 40%; }
  .skeleton-circle { width: 36px; height: 36px; border-radius: 50%; }
  .skeleton-block { height: 64px; margin-bottom: 12px; }

  /* Completion celebration */
  .completion-banner { text-align: center; padding: 24px; background: linear-gradient(135deg,
    rgba(63,185,80,0.08), rgba(88,166,255,0.08)); border: 1px solid rgba(63,185,80,0.2);
    border-radius: 12px; margin-top: 20px; }
  .completion-banner h3 { font-size: 16px; color: var(--green); margin-bottom: 4px; }
  .completion-banner p { font-size: 13px; color: var(--text2); }
  .completion-checkmark { width: 48px; height: 48px; margin: 0 auto 12px;
    animation: check-pop 0.5s cubic-bezier(0.175, 0.885, 0.32, 1.275) forwards; }
  @keyframes check-pop { 0% { transform: scale(0); opacity: 0; } 100% { transform: scale(1); opacity: 1; } }

  /* Live connection badge */
  .live-badge { display: inline-flex; align-items: center; gap: 5px; font-size: 10px; font-weight: 600;
    color: var(--green); text-transform: uppercase; letter-spacing: 0.5px; }
  .live-badge-dot { width: 6px; height: 6px; border-radius: 50%; background: var(--green);
    animation: live-dot 1.5s ease-in-out infinite; }

  /* ============================================================ */
  /* FILE TREE + PREVIEW (unchanged styles)                       */
  /* ============================================================ */

  .file-tree { font-size: 13px; font-family: 'SF Mono', 'Fira Code', monospace; }
  .tree-item { padding: 3px 8px; cursor: pointer; border-radius: 4px; display: flex;
               align-items: center; gap: 6px; user-select: none; }
  .tree-item:hover { background: var(--bg3); }
  .tree-item.active { background: rgba(88,166,255,0.1); color: var(--accent); }
  .tree-dir { font-weight: 600; }
  .tree-icon { width: 16px; text-align: center; flex-shrink: 0; }
  .tree-size { color: var(--text2); font-size: 11px; margin-left: auto; }
  .tree-children { padding-left: 16px; }

  .code-preview { background: var(--bg2); border: 1px solid var(--border); border-radius: 8px; overflow: hidden; }
  .code-header { padding: 8px 12px; background: var(--bg3); border-bottom: 1px solid var(--border);
                 display: flex; justify-content: space-between; align-items: center; font-size: 12px; }
  .code-header .lang-badge { background: var(--accent); color: #000; padding: 1px 8px;
                             border-radius: 3px; font-weight: 600; font-size: 10px; }
  .code-body { overflow: auto; max-height: 70vh; }
  .code-body pre { margin: 0; padding: 16px; font-size: 13px; line-height: 1.6; }
  .code-body pre code { font-family: 'SF Mono', 'Fira Code', 'Cascadia Code', monospace; }

  .md-preview { background: var(--bg2); border: 1px solid var(--border); border-radius: 8px;
                padding: 24px; max-height: 75vh; overflow: auto; }
  .md-preview h1, .md-preview h2, .md-preview h3 { border-bottom: 1px solid var(--border);
                                                     padding-bottom: 8px; margin: 16px 0 8px; }
  .md-preview code { background: var(--bg3); padding: 2px 6px; border-radius: 3px; font-size: 90%; }
  .md-preview pre { background: var(--bg3); padding: 12px; border-radius: 6px; overflow-x: auto; }
  .md-preview pre code { background: none; padding: 0; }
  .md-preview table { border-collapse: collapse; width: 100%; margin: 12px 0; }
  .md-preview th, .md-preview td { border: 1px solid var(--border); padding: 6px 12px; text-align: left; }
  .md-preview th { background: var(--bg3); }
  .md-preview img { max-width: 100%; border-radius: 6px; }
  .md-preview a { color: var(--accent); }
  .md-preview blockquote { border-left: 3px solid var(--border); padding-left: 12px; color: var(--text2); }

  .table-preview { overflow: auto; max-height: 70vh; }
  .table-preview table { border-collapse: collapse; width: 100%; font-size: 13px; }
  .table-preview th { background: var(--bg3); position: sticky; top: 0; z-index: 1; }
  .table-preview th, .table-preview td { border: 1px solid var(--border); padding: 6px 10px;
                                          text-align: left; white-space: nowrap; }
  .table-preview tr:hover { background: rgba(88,166,255,0.05); }
  .sheet-tabs { display: flex; gap: 4px; margin-bottom: 8px; }
  .sheet-tab { padding: 4px 12px; background: var(--bg3); border-radius: 4px 4px 0 0;
               cursor: pointer; font-size: 12px; border: 1px solid var(--border); }
  .sheet-tab.active { background: var(--bg2); border-bottom-color: var(--bg2); color: var(--accent); }

  .image-preview { text-align: center; padding: 20px; background: var(--bg2); border-radius: 8px;
                   border: 1px solid var(--border); }
  .image-preview img { max-width: 100%; max-height: 70vh; border-radius: 4px;
                       box-shadow: 0 4px 12px rgba(0,0,0,0.3); }

  .subtask { background: var(--bg2); border: 1px solid var(--border); border-radius: 8px;
             padding: 12px 16px; margin-bottom: 8px; }
  .subtask .st-header { display: flex; align-items: center; gap: 8px; }
  .subtask .st-idx { background: var(--bg3); width: 24px; height: 24px; border-radius: 50%;
                     display: flex; align-items: center; justify-content: center; font-size: 11px;
                     font-weight: 700; flex-shrink: 0; }
  .subtask .st-title { font-weight: 600; font-size: 14px; }
  .subtask .st-desc { color: var(--text2); font-size: 12px; margin-top: 4px; padding-left: 32px; }
  .subtask .st-files { font-size: 11px; color: var(--accent); margin-top: 4px; padding-left: 32px; }

  .html-preview-frame { width: 100%; min-height: 400px; max-height: 75vh; border: 1px solid var(--border);
                        border-radius: 8px; background: #fff; }
  .pdf-preview-frame { width: 100%; min-height: 500px; height: 75vh; border: 1px solid var(--border);
                       border-radius: 8px; }

  .nb-cell { margin-bottom: 12px; border: 1px solid var(--border); border-radius: 6px; overflow: hidden; }
  .nb-cell-header { padding: 4px 10px; font-size: 11px; color: var(--text2); background: var(--bg3); }
  .nb-cell-source { padding: 10px; font-size: 13px; }
  .nb-cell-output { padding: 10px; background: var(--bg); border-top: 1px solid var(--border); font-size: 13px; }

  .empty { text-align: center; padding: 60px 20px; color: var(--text2); }
  .empty svg { width: 64px; height: 64px; margin-bottom: 16px; opacity: 0.5; }
  .empty h3 { margin-bottom: 8px; }

  .loading { text-align: center; padding: 40px; color: var(--text2); }
  .spinner { display: inline-block; width: 24px; height: 24px; border: 3px solid var(--border);
             border-top-color: var(--accent); border-radius: 50%; animation: spin 0.8s linear infinite; }
  @keyframes spin { to { transform: rotate(360deg); } }

  .btn { padding: 6px 14px; border-radius: 6px; font-size: 12px; font-weight: 600; cursor: pointer;
         border: 1px solid var(--border); background: var(--bg3); color: var(--text);
         text-decoration: none; display: inline-flex; align-items: center; gap: 4px; }
  .btn:hover { background: var(--border); }
  .btn-primary { background: var(--accent); color: #000; border-color: var(--accent); }
  .btn-primary:hover { opacity: 0.9; }

  ::-webkit-scrollbar { width: 8px; height: 8px; }
  ::-webkit-scrollbar-track { background: transparent; }
  ::-webkit-scrollbar-thumb { background: var(--border); border-radius: 4px; }
  ::-webkit-scrollbar-thumb:hover { background: var(--text2); }
</style>
</head>
<body>
<div class="app">
  <!-- Sidebar -->
  <div class="sidebar">
    <div class="sidebar-header">
      <h2>Task Executions</h2>
    </div>
    <div class="exec-list" id="exec-list">
      <div class="loading"><div class="spinner"></div><p style="margin-top:8px">Loading...</p></div>
    </div>
  </div>

  <!-- Main content -->
  <div class="main">
    <div class="header">
      <h1>Oriexa Agent Dashboard</h1>
      <span class="badge">LIVE</span>
      <div style="margin-left:auto;display:flex;gap:8px;align-items:center">
        <span id="sse-status"></span>
        <button class="btn" onclick="loadExecutions()">Refresh</button>
      </div>
    </div>
    <div class="content" id="main-content">
      <div class="empty">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5">
          <path d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z"/>
        </svg>
        <h3>Select a task execution</h3>
        <p>Choose a task from the sidebar to preview agent outputs</p>
      </div>
    </div>
  </div>
</div>

<script>
const API = '/orchestrator/preview';
const PROGRESS_API = '/orchestrator/progress';
let currentExecId = null;
let currentFileData = null;
let sseConnection = null;
let progressSteps = [];

// Phase SVG icons (inline for no external deps)
const PHASE_ICONS = {
  triage: '<svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="3"/><path d="M12 1v2m0 18v2m-9-11h2m18 0h2m-4.2-6.8l-1.4 1.4M6.6 17.4l-1.4 1.4m0-12.8l1.4 1.4m10.8 10.8l1.4 1.4"/></svg>',
  clarification: '<svg viewBox="0 0 24 24"><path d="M21 11.5a8.38 8.38 0 01-.9 3.8 8.5 8.5 0 01-7.6 4.7 8.38 8.38 0 01-3.8-.9L3 21l1.9-5.7a8.38 8.38 0 01-.9-3.8 8.5 8.5 0 014.7-7.6 8.38 8.38 0 013.8-.9h.5a8.48 8.48 0 018 8v.5z"/></svg>',
  planning: '<svg viewBox="0 0 24 24"><polygon points="1 6 1 22 8 18 16 22 23 18 23 2 16 6 8 2 1 6"/><line x1="8" y1="2" x2="8" y2="18"/><line x1="16" y1="6" x2="16" y2="22"/></svg>',
  execution: '<svg viewBox="0 0 24 24"><polyline points="16 18 22 12 16 6"/><polyline points="8 6 2 12 8 18"/></svg>',
  complex_execution: '<svg viewBox="0 0 24 24"><path d="M12 2a9 9 0 019 9c0 3.9-3.1 7-6 9.3-1.2.9-2.2 1.7-3 1.7s-1.8-.8-3-1.7C6.1 18 3 14.9 3 11a9 9 0 019-9z"/><path d="M12 2c-1 2.8-1.5 5-1.5 8s.5 5.2 1.5 8c1-2.8 1.5-5 1.5-8s-.5-5.2-1.5-8z"/></svg>',
  review: '<svg viewBox="0 0 24 24"><path d="M22 11.08V12a10 10 0 11-5.93-9.14"/><polyline points="22 4 12 14.01 9 11.01"/></svg>',
  delivery: '<svg viewBox="0 0 24 24"><line x1="16.5" y1="9.4" x2="7.5" y2="4.21"/><path d="M21 16V8a2 2 0 00-1-1.73l-7-4a2 2 0 00-2 0l-7 4A2 2 0 003 8v8a2 2 0 001 1.73l7 4a2 2 0 002 0l7-4A2 2 0 0021 16z"/><polyline points="3.27 6.96 12 12.01 20.73 6.96"/><line x1="12" y1="22.08" x2="12" y2="12"/></svg>',
  failed: '<svg viewBox="0 0 24 24"><path d="M10.29 3.86L1.82 18a2 2 0 001.71 3h16.94a2 2 0 001.71-3L13.71 3.86a2 2 0 00-3.42 0z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/></svg>',
};

const PHASE_ORDER = ['triage','clarification','planning','execution','complex_execution','review','delivery'];
const PHASE_NAMES = {
  triage: 'Triage', clarification: 'Clarify', planning: 'Plan',
  execution: 'Build', complex_execution: 'Deep Build', review: 'Review', delivery: 'Deliver', failed: 'Failed'
};

// --- Data loading ---
async function loadExecutions() {
  const list = document.getElementById('exec-list');
  list.innerHTML = '<div class="loading"><div class="spinner"></div></div>';
  try {
    const resp = await fetch(`${API}/executions?limit=50`);
    const json = await resp.json();
    if (!json.ok || !json.data.length) {
      list.innerHTML = '<div class="empty"><h3>No executions yet</h3><p>Tasks will appear here once the agent starts working</p></div>';
      return;
    }
    list.innerHTML = json.data.map(ex => {
      const isLive = !['completed','failed'].includes(ex.status);
      return `
      <div class="exec-item ${ex.id === currentExecId ? 'active' : ''} ${isLive ? 'live-pulse' : ''}" onclick="loadExecution(${ex.id})">
        <div class="title">${esc(ex.task_title || 'Task #' + ex.oriexa_task_id)}</div>
        <div class="meta">
          <span class="status status-${ex.status}">${ex.status}</span>
          <span>${ex.file_count} files</span>
          <span>${formatTokens(ex.total_tokens_used)} tokens</span>
        </div>
      </div>`;
    }).join('');
  } catch (e) {
    list.innerHTML = `<div class="empty"><h3>Error loading</h3><p>${esc(e.message)}</p></div>`;
  }
}

async function loadExecution(id) {
  currentExecId = id;
  progressSteps = [];
  disconnectSSE();

  // Highlight in sidebar
  document.querySelectorAll('.exec-item').forEach(el => el.classList.remove('active'));
  document.querySelectorAll('.exec-item').forEach(el => {
    if (el.onclick && el.onclick.toString().includes(`(${id})`)) el.classList.add('active');
  });

  const main = document.getElementById('main-content');
  main.innerHTML = '<div class="loading"><div class="spinner"></div></div>';

  try {
    const resp = await fetch(`${API}/executions/${id}`);
    const json = await resp.json();
    if (!json.ok) throw new Error('Failed to load execution');
    renderExecution(json.data);

    // Load progress + start SSE if still active
    await loadProgress(id);
    const isActive = !['completed','failed'].includes(json.data.status);
    if (isActive) connectSSE(id);
  } catch (e) {
    main.innerHTML = `<div class="empty"><h3>Error</h3><p>${esc(e.message)}</p></div>`;
  }
}

async function loadProgress(execId) {
  try {
    const resp = await fetch(`${PROGRESS_API}/executions/${execId}`);
    const json = await resp.json();
    if (json.ok && json.data.steps) {
      progressSteps = json.data.steps;
      renderProgressPanel();
    }
  } catch (e) {
    // Non-blocking — progress may not be available
  }
}

function renderExecution(data) {
  const main = document.getElementById('main-content');
  const snap = data.task_snapshot || {};
  const isActive = !['completed','failed'].includes(data.status);

  main.innerHTML = `
    <div class="detail-header">
      <div class="info">
        <h2>${esc(snap.title || 'Task #' + data.oriexa_task_id)}</h2>
        <div class="desc">${esc((snap.description || '').substring(0, 300))}</div>
      </div>
      <span class="status status-${data.status}" style="font-size:12px;padding:4px 10px">${data.status}</span>
    </div>
    <div class="stats">
      <div class="stat-card"><div class="label">Tokens</div><div class="value">${formatTokens(data.total_tokens_used)}</div></div>
      <div class="stat-card"><div class="label">Files</div><div class="value">${countFiles(data.file_tree)}</div></div>
      <div class="stat-card"><div class="label">Subtasks</div><div class="value">${data.subtasks.length}</div></div>
      <div class="stat-card"><div class="label">Attempts</div><div class="value">${data.attempt_count}</div></div>
      ${data.claimed_credits ? `<div class="stat-card"><div class="label">Credits</div><div class="value">${data.claimed_credits}</div></div>` : ''}
    </div>
    ${data.error_message ? `<div style="background:rgba(248,81,73,0.1);border:1px solid var(--red);border-radius:8px;padding:12px;margin-bottom:16px;font-size:13px"><strong>Error:</strong> ${esc(data.error_message)}</div>` : ''}

    <div class="tabs">
      <div class="tab ${isActive ? 'active tab-live' : ''}" onclick="switchTab('progress')">Progress</div>
      <div class="tab ${isActive ? '' : 'active'}" onclick="switchTab('files')">Files</div>
      <div class="tab" onclick="switchTab('subtasks')">Subtasks (${data.subtasks.length})</div>
      <div class="tab" onclick="switchTab('details')">Details</div>
    </div>

    <div class="tab-content ${isActive ? 'active' : ''}" id="tab-progress">
      <div id="progress-panel">
        <div style="display:flex;flex-direction:column;gap:12px">
          <div class="skeleton skeleton-block" style="height:48px"></div>
          <div style="display:flex;justify-content:center;gap:24px">
            ${[1,2,3,4,5].map(() => '<div class="skeleton skeleton-circle"></div>').join('')}
          </div>
          <div class="skeleton skeleton-block" style="height:80px"></div>
          <div class="skeleton skeleton-block" style="height:80px"></div>
          <div class="skeleton skeleton-block" style="height:60px"></div>
        </div>
      </div>
    </div>

    <div class="tab-content ${isActive ? '' : 'active'}" id="tab-files">
      <div style="display:flex;gap:16px;height:calc(100vh - 340px)">
        <div style="width:280px;min-width:280px;overflow-y:auto;border:1px solid var(--border);border-radius:8px;padding:8px;background:var(--bg2)">
          <div class="file-tree" id="file-tree">${renderFileTree(data.file_tree, data.id)}</div>
        </div>
        <div style="flex:1;overflow:auto" id="file-preview">
          <div class="empty" style="padding:40px"><h3>Select a file</h3><p>Click a file in the tree to preview it</p></div>
        </div>
      </div>
    </div>

    <div class="tab-content" id="tab-subtasks">
      ${data.subtasks.length ? data.subtasks.map((st, i) => `
        <div class="subtask">
          <div class="st-header">
            <div class="st-idx">${st.order_index}</div>
            <div class="st-title">${esc(st.title)}</div>
            <span class="status status-${st.status}">${st.status}</span>
          </div>
          <div class="st-desc">${esc(st.description)}</div>
          ${st.files_changed && st.files_changed.length ? `<div class="st-files">Files: ${st.files_changed.map(f => esc(f)).join(', ')}</div>` : ''}
        </div>
      `).join('') : '<div class="empty"><h3>No subtasks</h3></div>'}
    </div>

    <div class="tab-content" id="tab-details">
      <div class="code-preview"><div class="code-header"><span>Execution Details (JSON)</span></div>
      <div class="code-body"><pre><code class="language-json">${esc(JSON.stringify({
        id: data.id, oriexa_task_id: data.oriexa_task_id, status: data.status,
        workspace_path: data.workspace_path, total_tokens_used: data.total_tokens_used,
        total_cost_usd: data.total_cost_usd, attempt_count: data.attempt_count,
        claimed_credits: data.claimed_credits, error_message: data.error_message,
        created_at: data.created_at, completed_at: data.completed_at,
      }, null, 2))}</code></pre></div></div>
    </div>
  `;
  hljs.highlightAll();
}


// ============================================================
// PROGRESS RENDERING
// ============================================================

function renderProgressPanel() {
  const panel = document.getElementById('progress-panel');
  if (!panel) return;

  if (!progressSteps.length) {
    panel.innerHTML = `
      <div style="display:flex;flex-direction:column;gap:12px">
        <div class="skeleton skeleton-block" style="height:48px"></div>
        <div style="display:flex;justify-content:center;gap:24px">
          ${[1,2,3,4,5].map(() => '<div class="skeleton skeleton-circle"></div>').join('')}
        </div>
        <div class="skeleton skeleton-block" style="height:80px"></div>
        <div class="skeleton skeleton-block" style="height:80px"></div>
      </div>`;
    return;
  }

  const latest = progressSteps[progressSteps.length - 1];
  const isComplete = ['delivery','failed'].includes(latest.phase);
  const isFailed = latest.phase === 'failed';
  const pct = latest.progress_pct || 0;

  // Determine which phases are done, active, or pending
  const seenPhases = new Set();
  let currentPhase = latest.phase;
  progressSteps.forEach(s => seenPhases.add(s.phase));

  // Build the pipeline — figure out which order to use
  // (skip clarification and complex_execution if not seen)
  const pipeline = PHASE_ORDER.filter(p => {
    if (p === 'clarification' && !seenPhases.has('clarification')) return false;
    if (p === 'complex_execution' && !seenPhases.has('complex_execution')) return false;
    if (p === 'execution' && seenPhases.has('complex_execution')) return false;
    return true;
  });

  // Pipeline HTML
  let pipelineHtml = '<div class="phase-pipeline">';
  pipeline.forEach((phase, i) => {
    let cls = '';
    const phaseIdx = pipeline.indexOf(phase);
    const currentIdx = pipeline.indexOf(currentPhase);
    if (isComplete && !isFailed) cls = 'done';
    else if (isFailed && phase === 'failed') cls = 'failed';
    else if (phase === currentPhase) cls = 'active';
    else if (phaseIdx < currentIdx) cls = 'done';

    pipelineHtml += `<div class="phase-pip ${cls}">
      <div class="phase-pip-icon">${PHASE_ICONS[phase] || PHASE_ICONS.execution}</div>
      <div class="phase-pip-label">${PHASE_NAMES[phase] || phase}</div>
    </div>`;
    if (i < pipeline.length - 1) {
      let connCls = '';
      if (isComplete && !isFailed) connCls = 'done';
      else if (phaseIdx < currentIdx) connCls = 'done';
      else if (phaseIdx === currentIdx) connCls = 'active';
      pipelineHtml += `<div class="phase-connector ${connCls}"></div>`;
    }
  });
  pipelineHtml += '</div>';

  // Progress bar
  const barHtml = `
    <div class="progress-bar-container">
      <div class="progress-bar-label">
        <span class="phase-label">${esc(latest.description)}</span>
        <span class="pct">${pct}%</span>
      </div>
      <div class="progress-bar-outer">
        <div class="progress-bar-inner ${!isComplete ? 'active' : ''}" style="width:${pct}%"></div>
      </div>
    </div>`;

  // Thinking indicator (only if not complete)
  let thinkingHtml = '';
  if (!isComplete) {
    thinkingHtml = `
    <div class="thinking-indicator">
      <div class="thinking-brain">${PHASE_ICONS[currentPhase] || PHASE_ICONS.execution}</div>
      <div class="thinking-text">
        <div class="thinking-title">${esc(latest.title)}
          <span class="thinking-dots"><span></span><span></span><span></span></span>
        </div>
        <div class="thinking-desc">${esc(latest.detail || latest.description)}</div>
      </div>
      <div class="live-badge"><div class="live-badge-dot"></div> LIVE</div>
    </div>`;
  }

  // Step timeline
  let timelineHtml = '<div class="step-timeline">';
  progressSteps.forEach((step, i) => {
    const isLast = i === progressSteps.length - 1;
    let entryCls = 'done';
    if (isLast && !isComplete) entryCls = 'active';
    if (isLast && isFailed) entryCls = 'failed';

    const time = new Date(step.timestamp * 1000);
    const timeStr = time.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });

    let metaChips = '';
    if (step.metadata) {
      const chips = [];
      if (step.metadata.complexity) chips.push(step.metadata.complexity);
      if (step.metadata.clarity_score) chips.push('clarity: ' + (step.metadata.clarity_score * 100).toFixed(0) + '%');
      if (step.metadata.subtask_count) chips.push(step.metadata.subtask_count + ' subtasks');
      if (step.metadata.score) chips.push('score: ' + step.metadata.score + '/100');
      if (step.metadata.files_created) chips.push(step.metadata.files_created + ' created');
      if (step.metadata.files_modified) chips.push(step.metadata.files_modified + ' modified');
      if (step.metadata.question_count) chips.push(step.metadata.question_count + ' questions');
      if (chips.length) {
        metaChips = '<div class="step-meta-chips">' + chips.map(c => `<span class="step-chip">${esc(c)}</span>`).join('') + '</div>';
      }
    }

    timelineHtml += `
    <div class="step-entry ${entryCls}" style="animation-delay:${i * 0.05}s">
      <div class="step-dot"></div>
      <div class="step-card">
        <div class="step-card-shimmer"></div>
        <span class="step-phase-badge badge-${step.phase}">${PHASE_NAMES[step.phase] || step.phase}</span>
        <div class="step-description">${esc(step.description)}</div>
        ${step.detail ? `<div class="step-detail">${esc(step.detail)}</div>` : ''}
        ${metaChips}
        <div class="step-time">${timeStr}</div>
      </div>
    </div>`;
  });
  timelineHtml += '</div>';

  // Completion banner
  let completionHtml = '';
  if (isComplete && !isFailed) {
    completionHtml = `
    <div class="completion-banner">
      <div class="completion-checkmark">
        <svg viewBox="0 0 48 48" width="48" height="48" fill="none" stroke="#3fb950" stroke-width="3" stroke-linecap="round">
          <circle cx="24" cy="24" r="20" opacity="0.2"/>
          <polyline points="14 24 22 32 34 16"/>
        </svg>
      </div>
      <h3>Task delivered successfully</h3>
      <p>All phases completed. Check the Files tab to explore everything the agent built.</p>
    </div>`;
  } else if (isFailed) {
    completionHtml = `
    <div class="completion-banner" style="border-color:rgba(248,81,73,0.2);background:linear-gradient(135deg,rgba(248,81,73,0.06),rgba(210,153,34,0.06))">
      <div class="completion-checkmark">
        <svg viewBox="0 0 48 48" width="48" height="48" fill="none" stroke="#f85149" stroke-width="3" stroke-linecap="round">
          <circle cx="24" cy="24" r="20" opacity="0.2"/>
          <line x1="16" y1="16" x2="32" y2="32"/><line x1="32" y1="16" x2="16" y2="32"/>
        </svg>
      </div>
      <h3>This task didn't make it across the finish line</h3>
      <p>Check the timeline above for details on what happened. Files may still be available for inspection.</p>
    </div>`;
  }

  panel.innerHTML = barHtml + pipelineHtml + thinkingHtml + timelineHtml + completionHtml;

  // Auto-scroll to latest step
  const entries = panel.querySelectorAll('.step-entry');
  if (entries.length) {
    entries[entries.length - 1].scrollIntoView({ behavior: 'smooth', block: 'nearest' });
  }
}


// ============================================================
// SSE — Live progress streaming
// ============================================================

function connectSSE(execId) {
  disconnectSSE();
  const statusEl = document.getElementById('sse-status');
  statusEl.innerHTML = '<span class="live-badge"><span class="live-badge-dot"></span> LIVE</span>';

  sseConnection = new EventSource(`${PROGRESS_API}/executions/${execId}/stream`);

  sseConnection.addEventListener('progress', (e) => {
    try {
      const step = JSON.parse(e.data);
      // Avoid duplicates by index
      if (step.index >= progressSteps.length) {
        progressSteps.push(step);
        renderProgressPanel();
      }
      // If final phase, close SSE
      if (['delivery','failed'].includes(step.phase)) {
        disconnectSSE();
        // Re-render execution to update status
        setTimeout(() => { loadExecution(execId); }, 1500);
      }
    } catch (err) {}
  });

  sseConnection.onerror = () => {
    statusEl.innerHTML = '';
    // Reconnect after 5s if still viewing this execution
    setTimeout(() => {
      if (currentExecId === execId) connectSSE(execId);
    }, 5000);
  };
}

function disconnectSSE() {
  if (sseConnection) {
    sseConnection.close();
    sseConnection = null;
  }
  const statusEl = document.getElementById('sse-status');
  if (statusEl) statusEl.innerHTML = '';
}


// ============================================================
// FILE TREE + PREVIEW (unchanged logic)
// ============================================================

function renderFileTree(tree, execId) {
  if (!tree || !tree.length) return '<div class="empty" style="padding:20px"><p>No files</p></div>';
  return tree.map(item => {
    if (item.type === 'directory') {
      return `
        <div class="tree-item tree-dir" onclick="this.nextElementSibling.style.display=this.nextElementSibling.style.display==='none'?'block':'none';event.stopPropagation()">
          <span class="tree-icon">&#128193;</span> ${esc(item.name)}
        </div>
        <div class="tree-children">${renderFileTree(item.children, execId)}</div>
      `;
    } else {
      const icon = getFileIcon(item.category);
      return `
        <div class="tree-item" onclick="previewFile(${execId}, '${escAttr(item.path)}', '${item.category}', '${item.language}')" title="${esc(item.path)} (${formatSize(item.size)})">
          <span class="tree-icon">${icon}</span> ${esc(item.name)}
          <span class="tree-size">${formatSize(item.size)}</span>
        </div>
      `;
    }
  }).join('');
}

function getFileIcon(category) {
  const icons = {
    code: '&#128196;', markdown: '&#128221;', html: '&#127760;', json: '&#123;&#125;',
    text: '&#128196;', csv: '&#128202;', spreadsheet: '&#128202;', image: '&#127912;',
    pdf: '&#128213;', notebook: '&#128211;', binary: '&#128190;'
  };
  return icons[category] || '&#128196;';
}

async function previewFile(execId, path, category, language) {
  const preview = document.getElementById('file-preview');
  document.querySelectorAll('#file-tree .tree-item').forEach(el => el.classList.remove('active'));
  event.currentTarget.classList.add('active');
  preview.innerHTML = '<div class="loading"><div class="spinner"></div></div>';

  if (category === 'image') {
    const url = `${API}/executions/${execId}/file?path=${encodeURIComponent(path)}`;
    preview.innerHTML = `
      <div class="image-preview">
        <img src="${url}" alt="${esc(path)}" />
        <div style="margin-top:12px">
          <a class="btn" href="${API}/executions/${execId}/download?path=${encodeURIComponent(path)}" download>Download</a>
        </div>
      </div>`;
    return;
  }
  if (category === 'pdf') {
    const url = `${API}/executions/${execId}/file?path=${encodeURIComponent(path)}`;
    preview.innerHTML = `<iframe class="pdf-preview-frame" src="${url}"></iframe>`;
    return;
  }

  try {
    const resp = await fetch(`${API}/executions/${execId}/file?path=${encodeURIComponent(path)}`);
    const json = await resp.json();
    if (!json.ok) throw new Error('Failed to load file');
    const d = json.data;

    if (d.error) {
      preview.innerHTML = `<div class="empty"><h3>Error</h3><p>${esc(d.error)}</p></div>`;
      return;
    }

    if (d.category === 'markdown') {
      preview.innerHTML = `
        <div style="display:flex;gap:8px;margin-bottom:8px">
          <button class="btn btn-primary" onclick="toggleMdView('rendered')">Rendered</button>
          <button class="btn" onclick="toggleMdView('source')">Source</button>
          <a class="btn" href="${API}/executions/${execId}/download?path=${encodeURIComponent(path)}" download style="margin-left:auto">Download</a>
        </div>
        <div id="md-rendered" class="md-preview">${marked.parse(d.content)}</div>
        <div id="md-source" style="display:none" class="code-preview">
          <div class="code-body"><pre><code class="language-markdown">${esc(d.content)}</code></pre></div>
        </div>`;
      hljs.highlightAll();
    } else if (d.category === 'html') {
      preview.innerHTML = `
        <div style="display:flex;gap:8px;margin-bottom:8px">
          <button class="btn btn-primary" onclick="toggleHtmlView('rendered')">Rendered</button>
          <button class="btn" onclick="toggleHtmlView('source')">Source</button>
          <a class="btn" href="${API}/executions/${execId}/download?path=${encodeURIComponent(path)}" download style="margin-left:auto">Download</a>
        </div>
        <iframe id="html-rendered" class="html-preview-frame" srcdoc="${escAttr(d.content)}"></iframe>
        <div id="html-source" style="display:none" class="code-preview">
          <div class="code-body"><pre><code class="language-html">${esc(d.content)}</code></pre></div>
        </div>`;
      hljs.highlightAll();
    } else if (d.category === 'spreadsheet') {
      renderSpreadsheet(preview, d);
    } else if (d.category === 'csv') {
      renderTable(preview, d.headers, d.rows, d.total_rows);
    } else if (d.category === 'notebook') {
      renderNotebook(preview, d);
    } else if (d.category === 'json') {
      let formatted = d.content;
      try { formatted = JSON.stringify(JSON.parse(d.content), null, 2); } catch {}
      preview.innerHTML = `
        <div class="code-preview">
          <div class="code-header">
            <span>${esc(d.name)} (${d.line_count} lines, ${formatSize(d.size)})</span>
            <span class="lang-badge">JSON</span>
          </div>
          <div class="code-body"><pre><code class="language-json">${esc(formatted)}</code></pre></div>
        </div>`;
      hljs.highlightAll();
    } else {
      const lang = d.language || language || '';
      preview.innerHTML = `
        <div class="code-preview">
          <div class="code-header">
            <span>${esc(d.name)} (${d.line_count} lines, ${formatSize(d.size)})</span>
            <div style="display:flex;gap:8px;align-items:center">
              ${lang ? `<span class="lang-badge">${lang.toUpperCase()}</span>` : ''}
              <a class="btn" href="${API}/executions/${execId}/download?path=${encodeURIComponent(path)}" download>Download</a>
            </div>
          </div>
          <div class="code-body"><pre><code class="${lang ? 'language-' + lang : ''}">${esc(d.content)}</code></pre></div>
        </div>`;
      hljs.highlightAll();
    }
  } catch (e) {
    preview.innerHTML = `<div class="empty"><h3>Preview Error</h3><p>${esc(e.message)}</p></div>`;
  }
}


// ============================================================
// SPECIAL RENDERERS
// ============================================================

function renderSpreadsheet(container, data) {
  const sheets = data.sheets || {};
  const names = Object.keys(sheets);
  if (!names.length) { container.innerHTML = '<div class="empty"><p>Empty spreadsheet</p></div>'; return; }
  let html = '<div class="sheet-tabs">';
  names.forEach((name, i) => {
    html += `<div class="sheet-tab ${i === 0 ? 'active' : ''}" onclick="showSheet('${escAttr(name)}')">${esc(name)}</div>`;
  });
  html += '</div>';
  names.forEach((name, i) => {
    const sheet = sheets[name];
    html += `<div class="sheet-content" id="sheet-${escAttr(name)}" style="${i > 0 ? 'display:none' : ''}">`;
    html += buildTableHtml(sheet.headers, sheet.rows);
    if (sheet.truncated) html += `<div style="padding:8px;font-size:12px;color:var(--text2)">Showing first 500 of ${sheet.total_rows} rows</div>`;
    html += '</div>';
  });
  container.innerHTML = html;
}

function renderTable(container, headers, rows, totalRows) {
  let html = `<div class="table-preview">${buildTableHtml(headers, rows)}</div>`;
  if (totalRows > rows.length) html += `<div style="padding:8px;font-size:12px;color:var(--text2)">Showing ${rows.length} of ${totalRows} rows</div>`;
  container.innerHTML = html;
}

function buildTableHtml(headers, rows) {
  let html = '<div class="table-preview"><table><thead><tr>';
  headers.forEach(h => html += `<th>${esc(h)}</th>`);
  html += '</tr></thead><tbody>';
  rows.forEach(row => {
    html += '<tr>';
    row.forEach(cell => html += `<td>${esc(cell)}</td>`);
    html += '</tr>';
  });
  html += '</tbody></table></div>';
  return html;
}

function renderNotebook(container, data) {
  if (!data.cells || !data.cells.length) { container.innerHTML = '<div class="empty"><p>Empty notebook</p></div>'; return; }
  let html = data.kernel ? `<div style="font-size:12px;color:var(--text2);margin-bottom:8px">Kernel: ${esc(data.kernel)}</div>` : '';
  data.cells.forEach((cell, i) => {
    html += `<div class="nb-cell">`;
    html += `<div class="nb-cell-header">${cell.cell_type === 'code' ? 'In [' + i + ']' : 'Markdown'}</div>`;
    if (cell.cell_type === 'code') {
      html += `<div class="nb-cell-source"><pre><code class="language-python">${esc(cell.source)}</code></pre></div>`;
    } else {
      html += `<div class="nb-cell-source md-preview">${marked.parse(cell.source)}</div>`;
    }
    if (cell.outputs && cell.outputs.length) {
      cell.outputs.forEach(out => {
        if (out.type === 'text') html += `<div class="nb-cell-output"><pre>${esc(out.text)}</pre></div>`;
        else if (out.type === 'html') html += `<div class="nb-cell-output">${out.html}</div>`;
        else if (out.type === 'image') html += `<div class="nb-cell-output"><img src="data:image/${out.format};base64,${out.data}" style="max-width:100%"/></div>`;
        else if (out.type === 'error') html += `<div class="nb-cell-output" style="color:var(--red)"><pre>${esc(out.ename + ': ' + out.evalue)}</pre></div>`;
      });
    }
    html += '</div>';
  });
  container.innerHTML = html;
  hljs.highlightAll();
}


// ============================================================
// TAB / VIEW SWITCHING
// ============================================================

function switchTab(name) {
  document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
  document.querySelectorAll('.tab-content').forEach(t => t.classList.remove('active'));
  event.currentTarget.classList.add('active');
  document.getElementById('tab-' + name).classList.add('active');
}

function toggleMdView(view) {
  document.getElementById('md-rendered').style.display = view === 'rendered' ? 'block' : 'none';
  document.getElementById('md-source').style.display = view === 'source' ? 'block' : 'none';
}

function toggleHtmlView(view) {
  document.getElementById('html-rendered').style.display = view === 'rendered' ? 'block' : 'none';
  document.getElementById('html-source').style.display = view === 'source' ? 'block' : 'none';
}

function showSheet(name) {
  document.querySelectorAll('.sheet-tab').forEach(t => t.classList.remove('active'));
  document.querySelectorAll('.sheet-content').forEach(t => t.style.display = 'none');
  event.currentTarget.classList.add('active');
  document.getElementById('sheet-' + name).style.display = 'block';
}


// ============================================================
// UTILITIES
// ============================================================

function esc(s) { if (!s) return ''; const d = document.createElement('div'); d.textContent = String(s); return d.innerHTML; }
function escAttr(s) { return String(s||'').replace(/"/g, '&quot;').replace(/'/g, '&#39;'); }
function formatSize(bytes) {
  if (!bytes) return '0 B';
  if (bytes < 1024) return bytes + ' B';
  if (bytes < 1024*1024) return (bytes/1024).toFixed(1) + ' KB';
  return (bytes/(1024*1024)).toFixed(1) + ' MB';
}
function formatTokens(n) { if (!n) return '0'; if (n > 1000000) return (n/1000000).toFixed(1) + 'M'; if (n > 1000) return (n/1000).toFixed(1) + 'K'; return String(n); }
function countFiles(tree) {
  if (!tree) return 0;
  let c = 0;
  tree.forEach(item => { if (item.type === 'file') c++; else if (item.children) c += countFiles(item.children); });
  return c;
}

// --- Init ---
loadExecutions();
setInterval(loadExecutions, 30000);
</script>
</body>
</html>"""


@router.get("/dashboard", response_class=HTMLResponse)
async def dashboard():
    """Serve the self-contained preview dashboard."""
    return HTMLResponse(content=DASHBOARD_HTML)
