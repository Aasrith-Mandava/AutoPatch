import { useState, useEffect } from 'react'
import './index.css'

const API = 'http://localhost:8000'

function App() {
  const [stage, setStage] = useState('idle')

  // Config
  const [projectKey, setProjectKey] = useState('')
  const [branch, setBranch] = useState('agent-sec-fixes')
  const [githubUser, setGithubUser] = useState('')
  const [repos, setRepos] = useState([])
  const [selectedRepo, setSelectedRepo] = useState(null)
  const [repoUrl, setRepoUrl] = useState('')

  // Data
  const [issues, setIssues] = useState([])
  const [filesToFix, setFilesToFix] = useState([])
  const [ruleCache, setRuleCache] = useState({})
  const [report, setReport] = useState(null)
  const [rejections, setRejections] = useState(new Set())
  const [finalizeResult, setFinalizeResult] = useState(null)

  // Progress
  const [progress, setProgress] = useState(0)
  const [progressSteps, setProgressSteps] = useState([])

  // UI
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)

  // Clear errors after 6 seconds
  useEffect(() => {
    if (error) {
      const t = setTimeout(() => setError(null), 6000)
      return () => clearTimeout(t)
    }
  }, [error])

  // ── API Calls ──
  const fetchRepos = async () => {
    if (!githubUser) return
    setLoading(true); setError(null)
    try {
      const res = await fetch(`${API}/api/repos/${githubUser}`)
      if (!res.ok) throw new Error('Could not fetch repos')
      setRepos(await res.json())
    } catch (e) { setError(e.message) }
    setLoading(false)
  }

  const scanIssues = async () => {
    if (!projectKey || !repoUrl) return
    setStage('scanning'); setError(null)
    try {
      const res = await fetch(`${API}/api/scan`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ project_key: projectKey, branch, repo_url: repoUrl }),
      })
      if (!res.ok) throw new Error('Scan failed')
      const data = await res.json()
      setIssues(data.issues); setFilesToFix(data.files_to_fix); setRuleCache(data.rule_cache)
      setStage('issues')
    } catch (e) { setError(e.message); setStage('idle') }
  }

  const runFix = async () => {
    setStage('fixing'); setProgress(0); setProgressSteps([])
    try {
      const res = await fetch(`${API}/api/fix`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ project_key: projectKey, branch, repo_url: repoUrl }),
      })
      const reader = res.body.getReader()
      const decoder = new TextDecoder()
      let buffer = ''
      while (true) {
        const { value, done } = await reader.read()
        if (done) break
        buffer += decoder.decode(value, { stream: true })
        const lines = buffer.split('\n'); buffer = lines.pop()
        for (const line of lines) {
          if (line.startsWith('data: ')) {
            const event = JSON.parse(line.slice(6))
            if (event.progress) setProgress(event.progress)
            if (event.message) setProgressSteps(prev => [...prev, event])
            if (event.status === 'complete') {
              const rpt = await (await fetch(`${API}/api/report`)).json()
              setReport(rpt); setStage('review')
            }
            if (event.status === 'error') { setError(event.message); setStage('issues') }
          }
        }
      }
    } catch (e) { setError(e.message); setStage('issues') }
  }

  const rejectFix = async (filePath) => {
    try {
      await fetch(`${API}/api/reject`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ file_path: filePath }),
      })
      setRejections(prev => new Set([...prev, filePath]))
    } catch (e) { setError(e.message) }
  }

  const finalize = async () => {
    setLoading(true)
    try {
      const res = await fetch(`${API}/api/finalize`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ branch, repo_url: repoUrl }),
      })
      setFinalizeResult(await res.json()); setStage('finalized')
    } catch (e) { setError(e.message) }
    setLoading(false)
  }

  const abort = async () => {
    await fetch(`${API}/api/abort`, { method: 'POST' })
    resetAll()
  }

  const resetAll = () => {
    setStage('idle'); setIssues([]); setFilesToFix([]); setRuleCache({})
    setReport(null); setRejections(new Set()); setFinalizeResult(null)
    setProgress(0); setProgressSteps([]); setError(null)
  }

  // ── Helpers ──
  const groupByFile = () => {
    const g = {}
    issues.forEach(i => { const fp = i.file_path || 'Unknown'; (g[fp] = g[fp] || []).push(i) })
    return g
  }

  const sevBadge = (s) => ({ CRITICAL: 'critical', BLOCKER: 'critical', MAJOR: 'major', MINOR: 'minor' }[(s || '').toUpperCase()] || 'info')
  const typeIcon = (t) => ({ bug: '🐛', vulnerability: '🔓', 'code smell': '🧹', 'security hotspot': '🔥' }[(t || '').replace('_', ' ').toLowerCase()] || '📌')

  const downloadReport = () => {
    if (!report) return
    let md = `# 🛡 AutoPatch Correction Report\n\n**Generated:** ${new Date().toLocaleString()}\n**Branch:** \`${branch}\`\n\n`
    md += `## Summary\n- Fixes Attempted: ${report.total_fixes_attempted || 0}\n- Successful: ${report.successful_fixes || 0}\n- Remaining: ${report.remaining_issues || 0}\n\n`
    for (const fix of (report.fixes || [])) {
      if (fix.status === 'success') {
        md += `### ${fix.file_path}\n\n`
        for (const fd of (fix.fix_details || [])) {
          md += `#### ${fd.issue_title} (\`${fd.rule_id}\`)\n**Root Cause:** ${fd.root_cause}\n\n\`\`\`\n${fd.original_snippet}\n\`\`\`\n→\n\`\`\`\n${fd.fixed_snippet}\n\`\`\`\n\n**What Changed:** ${fd.what_changed}\n**Benefit:** ${fd.benefit}\n\n---\n\n`
        }
      }
    }
    const a = document.createElement('a')
    a.href = URL.createObjectURL(new Blob([md], { type: 'text/markdown' }))
    a.download = `autopatch_report_${Date.now()}.md`; a.click()
  }

  // ═══════════════════ RENDER ═══════════════════
  return (
    <>
      <div className="bg-grid" />
      <div className="bg-glow" />
      <div className="bg-glow-2" />

      <div className="app-container">
        {/* ── Sidebar ── */}
        <aside className="sidebar">
          <div className="sidebar-logo">
            <div className="logo-icon">⚡</div>
            <div>
              <h1>AutoPatch</h1>
              <div className="subtitle">Autonomous Code Correction AI</div>
            </div>
          </div>

          <div className="sidebar-section-label">GitHub Integration</div>

          <div className="form-group">
            <label>GitHub Username</label>
            <input placeholder="e.g. Aasrith-Mandava" value={githubUser}
              onChange={e => setGithubUser(e.target.value)}
              onKeyDown={e => e.key === 'Enter' && fetchRepos()} />
          </div>

          {repos.length > 0 && (
            <div className="form-group">
              <label>Repository</label>
              <select value={selectedRepo?.name || ''} onChange={e => {
                const r = repos.find(x => x.name === e.target.value)
                setSelectedRepo(r); setRepoUrl(r ? r.html_url + '.git' : '')
              }}>
                <option value="">— Select —</option>
                {repos.map(r => <option key={r.name} value={r.name}>{r.name}</option>)}
              </select>
            </div>
          )}

          <button className="btn btn-ghost" onClick={fetchRepos} disabled={!githubUser || loading}>
            {loading ? '⏳' : '🔍'} Fetch Repos
          </button>

          <div className="sidebar-section-label" style={{ marginTop: '0.75rem' }}>Project Configuration</div>

          <div className="form-group">
            <label>SonarQube Project Key</label>
            <input placeholder="e.g. my-project" value={projectKey} onChange={e => setProjectKey(e.target.value)} />
          </div>

          <div className="form-group">
            <label>Target Branch</label>
            <input value={branch} onChange={e => setBranch(e.target.value)} />
          </div>

          <div style={{ flex: 1 }} />

          <button className="btn btn-primary" onClick={scanIssues}
            disabled={!projectKey || !repoUrl || stage === 'scanning' || stage === 'fixing'}>
            {stage === 'scanning' ? '⏳ Scanning...' : '🔍 Scan & Fetch Issues'}
          </button>

          {error && <div className="status-banner error">⚠️ {error}</div>}
        </aside>

        {/* ── Main ── */}
        <main className="main-content">

          {/* IDLE / WELCOME */}
          {stage === 'idle' && (
            <div className="welcome-container fade-in">
              <div className="welcome-icon">⚡</div>
              <h2>AutoPatch</h2>
              <p>Configure your project in the sidebar, connect to GitHub, and let the AI agent swarm detect and fix code issues autonomously.</p>
              <div className="welcome-features">
                <div className="welcome-feature">
                  <span className="feature-icon">🔍</span>
                  <span className="feature-title">Detect</span>
                  <span className="feature-desc">SonarQube analysis</span>
                </div>
                <div className="welcome-feature">
                  <span className="feature-icon">🤖</span>
                  <span className="feature-title">Fix</span>
                  <span className="feature-desc">LLM agent swarm</span>
                </div>
                <div className="welcome-feature">
                  <span className="feature-icon">👤</span>
                  <span className="feature-title">Review</span>
                  <span className="feature-desc">Human approval</span>
                </div>
                <div className="welcome-feature">
                  <span className="feature-icon">🚀</span>
                  <span className="feature-title">Deploy</span>
                  <span className="feature-desc">Git push & PR</span>
                </div>
              </div>
            </div>
          )}

          {/* SCANNING */}
          {stage === 'scanning' && (
            <div className="progress-container fade-in">
              <div className="spinner" />
              <h2>Scanning Repository</h2>
              <p style={{ color: 'var(--text-secondary)', marginTop: '0.5rem' }}>
                Connecting to SonarQube and analysing your codebase...
              </p>
            </div>
          )}

          {/* ISSUES DASHBOARD */}
          {stage === 'issues' && (
            <div className="fade-in">
              <div className="section-header">
                <h2>📥 Anomalies Detected</h2>
                <p>SonarQube found {issues.length} issue{issues.length !== 1 ? 's' : ''} across {filesToFix.length} file{filesToFix.length !== 1 ? 's' : ''}.</p>
              </div>

              <div className="metrics-grid">
                <div className="metric-card">
                  <div className="metric-value">{issues.length}</div>
                  <div className="metric-label">Total Issues</div>
                </div>
                <div className="metric-card">
                  <div className="metric-value">{filesToFix.length}</div>
                  <div className="metric-label">Files Affected</div>
                </div>
                <div className="metric-card">
                  <div className="metric-value red">
                    {issues.filter(i => ['CRITICAL','BLOCKER'].includes((i.severity||'').toUpperCase())).length}
                  </div>
                  <div className="metric-label">Critical</div>
                </div>
                <div className="metric-card">
                  <div className="metric-value yellow">
                    {issues.filter(i => (i.severity||'').toUpperCase() === 'MAJOR').length}
                  </div>
                  <div className="metric-label">Major</div>
                </div>
              </div>

              {Object.entries(groupByFile()).map(([fp, fi], idx) => (
                <FileGroup key={fp} filePath={fp} issues={fi} ruleCache={ruleCache}
                  sevBadge={sevBadge} typeIcon={typeIcon} delay={idx * 80} />
              ))}

              <div style={{ marginTop: '2rem' }}>
                <button className="btn btn-primary" onClick={runFix} style={{ maxWidth: '400px' }}>
                  🛠 Auto-Fix All Issues
                </button>
              </div>
            </div>
          )}

          {/* FIXING */}
          {stage === 'fixing' && (
            <div className="progress-container fade-in">
              <h2>🤖 Agent Swarm Active</h2>
              <p style={{ color: 'var(--text-secondary)', marginTop: '0.5rem' }}>
                LangGraph workers are analysing and patching your code in parallel.
              </p>
              <div className="progress-bar-track">
                <div className="progress-bar-fill" style={{ width: `${progress}%` }} />
              </div>
              <div className="progress-steps">
                {progressSteps.map((s, i) => (
                  <div key={i} className="progress-step done">
                    <div className="step-icon">✓</div>
                    <span>{s.message}</span>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* REVIEW (HITL) */}
          {stage === 'review' && report && (
            <div className="fade-in">
              <div className="status-banner warning">
                ⚠️ <strong>Human Approval Required</strong> — Review each fix below. Reject any you disagree with before finalizing.
              </div>

              <div className="section-header">
                <h2>📊 Fix Review & Approval</h2>
                <p>The agent swarm has completed. Inspect every change before committing.</p>
              </div>

              <div className="metrics-grid">
                <div className="metric-card">
                  <div className="metric-value">{report.total_fixes_attempted || 0}</div>
                  <div className="metric-label">Fixes Attempted</div>
                </div>
                <div className="metric-card">
                  <div className="metric-value green">{report.successful_fixes || 0}</div>
                  <div className="metric-label">Successful</div>
                </div>
                <div className="metric-card">
                  <div className="metric-value yellow">{report.remaining_issues || 0}</div>
                  <div className="metric-label">Remaining</div>
                </div>
              </div>

              {(report.fixes || []).filter(f => f.status === 'success').map((fix, i) => (
                <FixCard key={i} fix={fix} rejected={rejections.has(fix.file_path)} onReject={() => rejectFix(fix.file_path)} />
              ))}

              <div className="action-bar">
                <button className="btn btn-primary" onClick={finalize} disabled={loading}>
                  {loading ? '⏳ Pushing...' : '✅ Finalize & Push to GitHub'}
                </button>
                <button className="btn btn-danger" onClick={abort}>❌ Abort Everything</button>
              </div>
            </div>
          )}

          {/* FINALIZED */}
          {stage === 'finalized' && finalizeResult && (
            <div className="finalize-container fade-in">
              <div className="finalize-icon">🎉</div>
              <h2>Fixes Deployed Successfully</h2>
              <p style={{ color: 'var(--text-secondary)', marginBottom: '0.5rem', fontSize: '1rem' }}>
                AutoPatch pushed all approved fixes to branch <code style={{ color: 'var(--accent-light)', fontFamily: "'JetBrains Mono', monospace", fontSize: '0.9rem' }}>{branch}</code>
              </p>

              {finalizeResult.pr_link && (
                <a href={finalizeResult.pr_link} target="_blank" rel="noreferrer" className="pr-link">
                  🔗 Open Pull Request on GitHub
                </a>
              )}

              <div style={{ display: 'flex', gap: '1rem', justifyContent: 'center', marginTop: '2rem' }}>
                <button className="btn btn-ghost btn-sm" onClick={downloadReport}>📥 Download Report</button>
                <button className="btn btn-ghost btn-sm" onClick={resetAll}>🔄 Start New Scan</button>
              </div>
            </div>
          )}
        </main>
      </div>
    </>
  )
}


// ═══════════════════ SUB-COMPONENTS ═══════════════════

function FileGroup({ filePath, issues, ruleCache, sevBadge, typeIcon, delay }) {
  const [open, setOpen] = useState(true)

  return (
    <div className="file-group slide-in" style={{ animationDelay: `${delay}ms` }}>
      <div className="file-group-header" onClick={() => setOpen(!open)}>
        <span className="file-icon">📄</span>
        <span className="file-name">{filePath}</span>
        <span className="issue-count">{issues.length} issue{issues.length !== 1 ? 's' : ''}</span>
        <span className="chevron">{open ? '▼' : '▶'}</span>
      </div>
      {open && (
        <div className="file-group-body">
          {issues.map((issue, i) => {
            const rule = ruleCache[issue.rule] || {}
            const it = (issue.issue_type || '').replace('_', ' ')
            return (
              <div key={i} className="issue-card">
                <div className="issue-header">
                  <span className={`badge badge-${sevBadge(issue.severity)}`}>{(issue.severity || '?').toUpperCase()}</span>
                  <span className={`badge badge-${(issue.issue_type || '').toLowerCase()}`}>{typeIcon(issue.issue_type)} {it}</span>
                  <span className="line-num">L{issue.line || '?'}</span>
                  <code style={{ fontSize: '0.7rem', color: 'var(--text-muted)' }}>{issue.rule}</code>
                </div>
                <div className="issue-message"><strong>Issue:</strong> {issue.message || 'No message'}</div>
                {rule.description && (
                  <div className="issue-explanation">
                    <strong>Why is this a problem?</strong><br/>
                    {rule.description.length > 400 ? rule.description.slice(0, 400) + '...' : rule.description}
                  </div>
                )}
                {issue.effort && <div className="issue-effort">⏱ Estimated effort: <strong>{issue.effort}</strong></div>}
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}


function FixCard({ fix, rejected, onReject }) {
  const [open, setOpen] = useState(!rejected)
  const [diffOpen, setDiffOpen] = useState(false)
  const details = fix.fix_details || []

  return (
    <div className={`fix-card ${rejected ? 'rejected' : ''}`}>
      <div className="fix-card-header" onClick={() => setOpen(!open)}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '10px', flexWrap: 'wrap' }}>
          <h3>{fix.file_path}</h3>
          {rejected && <span className="badge badge-rejected">Rejected</span>}
          {fix.flagged_by_judge && !rejected && <span className="badge badge-flagged">⚠️ Flagged by Judge</span>}
          {!rejected && !fix.flagged_by_judge && <span className="badge badge-approved">✓ Pending Approval</span>}
          <span style={{ fontSize: '0.72rem', color: 'var(--text-muted)' }}>{details.length} fix{details.length !== 1 ? 'es' : ''}</span>
        </div>
        <span style={{ color: 'var(--text-muted)', fontSize: '0.8rem' }}>{open ? '▼' : '▶'}</span>
      </div>

      {open && (
        <div className="fix-card-body">
          {fix.flagged_by_judge && (
            <div className="status-banner warning" style={{ marginBottom: '1.25rem' }}>
              ⚠️ <strong>Judge Warning:</strong> {fix.judge_rationale || 'Business logic may have been altered. Review carefully.'}
            </div>
          )}

          {details.map((fd, i) => (
            <div key={i} className="fix-detail">
              <div className="fix-detail-title">
                <span className={`badge badge-${(fd.severity || '').toLowerCase().replace(' ', '-')}`}>{fd.severity}</span>
                Fix {i + 1}: {fd.issue_title}
                <code style={{ fontSize: '0.7rem', color: 'var(--text-muted)', marginLeft: 'auto' }}>{fd.rule_id}</code>
              </div>

              <div className="fix-section">
                <div className="fix-section-label">🔍 Root Cause</div>
                <div className="fix-section-content fix-root-cause">{fd.root_cause}</div>
              </div>

              <div className="snippets-grid">
                <div className="snippet-box before">
                  <div className="snippet-label">❌ Before (Problematic)</div>
                  <pre>{fd.original_snippet}</pre>
                </div>
                <div className="snippet-box after">
                  <div className="snippet-label">✅ After (Fixed)</div>
                  <pre>{fd.fixed_snippet}</pre>
                </div>
              </div>

              <div className="fix-section" style={{ marginTop: '1rem' }}>
                <div className="fix-section-label">🔄 What Changed</div>
                <div className="fix-section-content fix-what-changed">{fd.what_changed}</div>
              </div>

              <div className="fix-section">
                <div className="fix-section-label">📈 Benefit</div>
                <div className="fix-section-content fix-benefit">{fd.benefit}</div>
              </div>
            </div>
          ))}

          {fix.diff_data?.diff && (
            <div className="diff-viewer">
              <div className="diff-viewer-header" onClick={() => setDiffOpen(!diffOpen)}>
                <span>{diffOpen ? '▼' : '▶'}</span> Full Unified Diff
              </div>
              {diffOpen && <pre>{fix.diff_data.diff}</pre>}
            </div>
          )}

          {!rejected && (
            <button className="btn btn-danger btn-sm" style={{ marginTop: '1.25rem', maxWidth: '250px' }} onClick={onReject}>
              ❌ Reject This Fix
            </button>
          )}
          {rejected && (
            <div className="status-banner error" style={{ marginTop: '1rem' }}>This fix was rejected and reverted to baseline.</div>
          )}
        </div>
      )}
    </div>
  )
}


export default App
