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
    --red: #f85149; --yellow: #d29922; --purple: #bc8cff;
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
               border: 1px solid transparent; }
  .exec-item:hover { background: var(--bg3); border-color: var(--border); }
  .exec-item.active { background: var(--bg3); border-color: var(--accent); }
  .exec-item .title { font-size: 13px; font-weight: 600; white-space: nowrap; overflow: hidden;
                      text-overflow: ellipsis; }
  .exec-item .meta { font-size: 11px; color: var(--text2); display: flex; gap: 8px; margin-top: 3px; }
  .exec-item .status { display: inline-block; padding: 1px 6px; border-radius: 3px; font-size: 10px;
                       font-weight: 600; text-transform: uppercase; }
  .status-completed { background: rgba(63,185,80,0.15); color: var(--green); }
  .status-failed { background: rgba(248,81,73,0.15); color: var(--red); }
  .status-executing, .status-planning { background: rgba(88,166,255,0.15); color: var(--accent); }
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
         border-bottom: 2px solid transparent; color: var(--text2); }
  .tab:hover { color: var(--text); }
  .tab.active { color: var(--accent); border-bottom-color: var(--accent); }
  .tab-content { display: none; }
  .tab-content.active { display: block; }

  /* File tree */
  .file-tree { font-size: 13px; font-family: 'SF Mono', 'Fira Code', monospace; }
  .tree-item { padding: 3px 8px; cursor: pointer; border-radius: 4px; display: flex;
               align-items: center; gap: 6px; user-select: none; }
  .tree-item:hover { background: var(--bg3); }
  .tree-item.active { background: rgba(88,166,255,0.1); color: var(--accent); }
  .tree-dir { font-weight: 600; }
  .tree-icon { width: 16px; text-align: center; flex-shrink: 0; }
  .tree-size { color: var(--text2); font-size: 11px; margin-left: auto; }
  .tree-children { padding-left: 16px; }

  /* Code preview */
  .code-preview { background: var(--bg2); border: 1px solid var(--border); border-radius: 8px;
                  overflow: hidden; }
  .code-header { padding: 8px 12px; background: var(--bg3); border-bottom: 1px solid var(--border);
                 display: flex; justify-content: space-between; align-items: center; font-size: 12px; }
  .code-header .lang-badge { background: var(--accent); color: #000; padding: 1px 8px;
                             border-radius: 3px; font-weight: 600; font-size: 10px; }
  .code-body { overflow: auto; max-height: 70vh; }
  .code-body pre { margin: 0; padding: 16px; font-size: 13px; line-height: 1.6; }
  .code-body pre code { font-family: 'SF Mono', 'Fira Code', 'Cascadia Code', monospace; }

  /* Markdown preview */
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

  /* Table preview (CSV/XLSX) */
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

  /* Image preview */
  .image-preview { text-align: center; padding: 20px; background: var(--bg2); border-radius: 8px;
                   border: 1px solid var(--border); }
  .image-preview img { max-width: 100%; max-height: 70vh; border-radius: 4px;
                       box-shadow: 0 4px 12px rgba(0,0,0,0.3); }

  /* Subtasks */
  .subtask { background: var(--bg2); border: 1px solid var(--border); border-radius: 8px;
             padding: 12px 16px; margin-bottom: 8px; }
  .subtask .st-header { display: flex; align-items: center; gap: 8px; }
  .subtask .st-idx { background: var(--bg3); width: 24px; height: 24px; border-radius: 50%;
                     display: flex; align-items: center; justify-content: center; font-size: 11px;
                     font-weight: 700; flex-shrink: 0; }
  .subtask .st-title { font-weight: 600; font-size: 14px; }
  .subtask .st-desc { color: var(--text2); font-size: 12px; margin-top: 4px; padding-left: 32px; }
  .subtask .st-files { font-size: 11px; color: var(--accent); margin-top: 4px; padding-left: 32px; }

  /* HTML preview */
  .html-preview-frame { width: 100%; min-height: 400px; max-height: 75vh; border: 1px solid var(--border);
                        border-radius: 8px; background: #fff; }

  /* PDF preview */
  .pdf-preview-frame { width: 100%; min-height: 500px; height: 75vh; border: 1px solid var(--border);
                       border-radius: 8px; }

  /* Notebook */
  .nb-cell { margin-bottom: 12px; border: 1px solid var(--border); border-radius: 6px; overflow: hidden; }
  .nb-cell-header { padding: 4px 10px; font-size: 11px; color: var(--text2); background: var(--bg3); }
  .nb-cell-source { padding: 10px; font-size: 13px; }
  .nb-cell-output { padding: 10px; background: var(--bg); border-top: 1px solid var(--border); font-size: 13px; }

  /* Empty state */
  .empty { text-align: center; padding: 60px 20px; color: var(--text2); }
  .empty svg { width: 64px; height: 64px; margin-bottom: 16px; opacity: 0.5; }
  .empty h3 { margin-bottom: 8px; }

  /* Loading */
  .loading { text-align: center; padding: 40px; color: var(--text2); }
  .spinner { display: inline-block; width: 24px; height: 24px; border: 3px solid var(--border);
             border-top-color: var(--accent); border-radius: 50%; animation: spin 0.8s linear infinite; }
  @keyframes spin { to { transform: rotate(360deg); } }

  /* Download button */
  .btn { padding: 6px 14px; border-radius: 6px; font-size: 12px; font-weight: 600; cursor: pointer;
         border: 1px solid var(--border); background: var(--bg3); color: var(--text);
         text-decoration: none; display: inline-flex; align-items: center; gap: 4px; }
  .btn:hover { background: var(--border); }
  .btn-primary { background: var(--accent); color: #000; border-color: var(--accent); }
  .btn-primary:hover { opacity: 0.9; }

  /* Scrollbar */
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
      <span class="badge">PREVIEW</span>
      <div style="margin-left:auto">
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
let currentExecId = null;
let currentFileData = null;

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
    list.innerHTML = json.data.map(ex => `
      <div class="exec-item ${ex.id === currentExecId ? 'active' : ''}" onclick="loadExecution(${ex.id})">
        <div class="title">${esc(ex.task_title || 'Task #' + ex.oriexa_task_id)}</div>
        <div class="meta">
          <span class="status status-${ex.status}">${ex.status}</span>
          <span>${ex.file_count} files</span>
          <span>${formatTokens(ex.total_tokens_used)} tokens</span>
        </div>
      </div>
    `).join('');
  } catch (e) {
    list.innerHTML = `<div class="empty"><h3>Error loading</h3><p>${esc(e.message)}</p></div>`;
  }
}

async function loadExecution(id) {
  currentExecId = id;
  // Highlight in sidebar
  document.querySelectorAll('.exec-item').forEach(el => el.classList.remove('active'));
  document.querySelectorAll('.exec-item').forEach(el => {
    if (el.onclick.toString().includes(`(${id})`)) el.classList.add('active');
  });

  const main = document.getElementById('main-content');
  main.innerHTML = '<div class="loading"><div class="spinner"></div></div>';

  try {
    const resp = await fetch(`${API}/executions/${id}`);
    const json = await resp.json();
    if (!json.ok) throw new Error('Failed to load execution');
    renderExecution(json.data);
  } catch (e) {
    main.innerHTML = `<div class="empty"><h3>Error</h3><p>${esc(e.message)}</p></div>`;
  }
}

function renderExecution(data) {
  const main = document.getElementById('main-content');
  const snap = data.task_snapshot || {};

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
      <div class="tab active" onclick="switchTab('files')">Files</div>
      <div class="tab" onclick="switchTab('subtasks')">Subtasks (${data.subtasks.length})</div>
      <div class="tab" onclick="switchTab('details')">Details</div>
    </div>

    <div class="tab-content active" id="tab-files">
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

// --- File tree ---
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

// --- File preview ---
async function previewFile(execId, path, category, language) {
  const preview = document.getElementById('file-preview');
  // Highlight active file
  document.querySelectorAll('#file-tree .tree-item').forEach(el => el.classList.remove('active'));
  event.currentTarget.classList.add('active');

  preview.innerHTML = '<div class="loading"><div class="spinner"></div></div>';

  // Images, PDFs — render directly
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
      // Code / text
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

// --- Special renderers ---
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

// --- Tab / view switching ---
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

// --- Utilities ---
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
// Auto-refresh every 30s
setInterval(loadExecutions, 30000);
</script>
</body>
</html>"""


@router.get("/dashboard", response_class=HTMLResponse)
async def dashboard():
    """Serve the self-contained preview dashboard."""
    return HTMLResponse(content=DASHBOARD_HTML)
