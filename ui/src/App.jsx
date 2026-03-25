import { useState } from 'react'
import './index.css'

const API = 'http://localhost:8000'

function App() {
  // Workflow state: idle → scanning → issues → fixing → review → finalized
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

  // Loading
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)

  // ── Fetch Repos ──
  const fetchRepos = async () => {
    if (!githubUser) return
    setLoading(true)
    setError(null)
    try {
      const res = await fetch(`${API}/api/repos/${githubUser}`)
      if (!res.ok) throw new Error('Could not fetch repos')
      const data = await res.json()
      setRepos(data)
    } catch (e) {
      setError(e.message)
    }
    setLoading(false)
  }

  // ── Scan Issues ──
  const scanIssues = async () => {
    if (!projectKey || !repoUrl) return
    setStage('scanning')
    setError(null)
    try {
      const res = await fetch(`${API}/api/scan`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ project_key: projectKey, branch, repo_url: repoUrl }),
      })
      if (!res.ok) throw new Error('Scan failed')
      const data = await res.json()
      setIssues(data.issues)
      setFilesToFix(data.files_to_fix)
      setRuleCache(data.rule_cache)
      setStage('issues')
    } catch (e) {
      setError(e.message)
      setStage('idle')
    }
  }

  // ── Run Fix (SSE) ──
  const runFix = async () => {
    setStage('fixing')
    setProgress(0)
    setProgressSteps([])

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

        const lines = buffer.split('\n')
        buffer = lines.pop()

        for (const line of lines) {
          if (line.startsWith('data: ')) {
            const event = JSON.parse(line.slice(6))
            if (event.progress) setProgress(event.progress)
            if (event.message) {
              setProgressSteps(prev => [...prev, { node: event.node, message: event.message, status: event.status }])
            }
            if (event.status === 'complete') {
              // Fetch the full report
              const reportRes = await fetch(`${API}/api/report`)
              const reportData = await reportRes.json()
              setReport(reportData)
              setStage('review')
            }
            if (event.status === 'error') {
              setError(event.message)
              setStage('issues')
            }
          }
        }
      }
    } catch (e) {
      setError(e.message)
      setStage('issues')
    }
  }

  // ── Reject Fix ──
  const rejectFix = async (filePath) => {
    try {
      await fetch(`${API}/api/reject`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ file_path: filePath }),
      })
      setRejections(prev => new Set([...prev, filePath]))
    } catch (e) {
      setError(e.message)
    }
  }

  // ── Finalize ──
  const finalize = async () => {
    setLoading(true)
    try {
      const res = await fetch(`${API}/api/finalize`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ branch, repo_url: repoUrl }),
      })
      const data = await res.json()
      setFinalizeResult(data)
      setStage('finalized')
    } catch (e) {
      setError(e.message)
    }
    setLoading(false)
  }

  // ── Abort ──
  const abort = async () => {
    await fetch(`${API}/api/abort`, { method: 'POST' })
    setStage('idle')
    setReport(null)
    setRejections(new Set())
  }

  // ── Reset ──
  const resetAll = () => {
    setStage('idle')
    setIssues([])
    setFilesToFix([])
    setRuleCache({})
    setReport(null)
    setRejections(new Set())
    setFinalizeResult(null)
    setProgress(0)
    setProgressSteps([])
    setError(null)
  }

  // ── Helpers ──
  const groupIssuesByFile = () => {
    const groups = {}
    issues.forEach(issue => {
      const fp = issue.file_path || 'Unknown'
      if (!groups[fp]) groups[fp] = []
      groups[fp].push(issue)
    })
    return groups
  }

  const getSeverityBadge = (severity) => {
    const s = (severity || '').toUpperCase()
    const map = { CRITICAL: 'critical', BLOCKER: 'critical', MAJOR: 'major', MINOR: 'minor', INFO: 'info' }
    return map[s] || 'info'
  }

  const getTypeIcon = (type) => {
    const t = (type || '').replace('_', ' ').toLowerCase()
    const map = { bug: '🐛', vulnerability: '🔓', 'code smell': '🧹', 'security hotspot': '🔥' }
    return map[t] || '📌'
  }

  const downloadReport = () => {
    if (!report) return
    let md = `# 🛡 AutoPatch Autonomous Correction Report\n\n`
    md += `**Generated on:** ${new Date().toLocaleString()}\n`
    md += `**Branch:** \`${branch}\`\n\n`
    md += `## Summary\n`
    md += `- Fixes Attempted: ${report.total_fixes_attempted || 0}\n`
    md += `- Successful: ${report.successful_fixes || 0}\n`
    md += `- Remaining: ${report.remaining_issues || 0}\n\n`

    for (const fix of (report.fixes || [])) {
      if (fix.status === 'success') {
        md += `### ${fix.file_path}\n\n`
        for (const fd of (fix.fix_details || [])) {
          md += `#### ${fd.issue_title} (\`${fd.rule_id}\`)\n`
          md += `**Root Cause:** ${fd.root_cause}\n\n`
          md += `**Before:**\n\`\`\`\n${fd.original_snippet}\n\`\`\`\n\n`
          md += `**After:**\n\`\`\`\n${fd.fixed_snippet}\n\`\`\`\n\n`
          md += `**What Changed:** ${fd.what_changed}\n\n`
          md += `**Benefit:** ${fd.benefit}\n\n---\n\n`
        }
      }
    }

    const blob = new Blob([md], { type: 'text/markdown' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `autopatch_report_${Date.now()}.md`
    a.click()
    URL.revokeObjectURL(url)
  }

  // ═══════════════════════════════════════════════════════
  //  RENDER
  // ═══════════════════════════════════════════════════════

  return (
    <div className="app-container">
      {/* ── Sidebar ── */}
      <aside className="sidebar">
        <div className="sidebar-logo">
          <div className="logo-icon">⚡</div>
          <div>
            <h1>AutoPatch</h1>
            <span>Autonomous Code Correction</span>
          </div>
        </div>

        <div className="form-group">
          <label>SonarQube Project Key</label>
          <input
            type="text"
            placeholder="e.g. my-project"
            value={projectKey}
            onChange={e => setProjectKey(e.target.value)}
          />
        </div>

        <div className="form-group">
          <label>Target Branch</label>
          <input
            type="text"
            value={branch}
            onChange={e => setBranch(e.target.value)}
          />
        </div>

        <div style={{ borderTop: '1px solid var(--border)', paddingTop: '1rem' }}>
          <div className="form-group">
            <label>GitHub Username</label>
            <input
              type="text"
              placeholder="e.g. Aasrith-Mandava"
              value={githubUser}
              onChange={e => setGithubUser(e.target.value)}
              onKeyDown={e => e.key === 'Enter' && fetchRepos()}
            />
          </div>

          {repos.length > 0 && (
            <div className="form-group" style={{ marginTop: '0.75rem' }}>
              <label>Select Repository</label>
              <select
                value={selectedRepo?.name || ''}
                onChange={e => {
                  const r = repos.find(repo => repo.name === e.target.value)
                  setSelectedRepo(r)
                  setRepoUrl(r ? r.html_url + '.git' : '')
                }}
              >
                <option value="">— Choose —</option>
                {repos.map(r => (
                  <option key={r.name} value={r.name}>{r.name}</option>
                ))}
              </select>
            </div>
          )}

          <button
            className="btn btn-ghost"
            style={{ marginTop: '0.75rem' }}
            onClick={fetchRepos}
            disabled={!githubUser || loading}
          >
            {loading ? '⏳' : '🔍'} Fetch Repos
          </button>
        </div>

        <button
          className="btn btn-primary"
          onClick={scanIssues}
          disabled={!projectKey || !repoUrl || stage === 'scanning' || stage === 'fixing'}
        >
          {stage === 'scanning' ? '⏳ Scanning...' : '🔍 Fetch Issues'}
        </button>

        {error && (
          <div className="status-banner error">
            ⚠️ {error}
          </div>
        )}
      </aside>

      {/* ── Main Content ── */}
      <main className="main-content">

        {/* IDLE STATE */}
        {stage === 'idle' && (
          <div className="empty-state fade-in">
            <div className="empty-icon">⚡</div>
            <h3>Welcome to AutoPatch</h3>
            <p>Configure your project in the sidebar and click "Fetch Issues" to begin.</p>
          </div>
        )}

        {/* SCANNING STATE */}
        {stage === 'scanning' && (
          <div className="progress-container fade-in">
            <div className="spinner"></div>
            <h2 style={{ fontSize: '1.3rem', marginBottom: '0.5rem' }}>Scanning Repository...</h2>
            <p style={{ color: 'var(--text-secondary)' }}>Connecting to SonarQube and analysing your codebase.</p>
          </div>
        )}

        {/* ISSUES DASHBOARD */}
        {stage === 'issues' && (
          <div className="fade-in">
            <div className="section-header">
              <h2>📥 Repository Anomalies Detected</h2>
              <p>SonarQube found the following issues in your codebase.</p>
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
                <div className="metric-value" style={{ color: 'var(--red)' }}>
                  {issues.filter(i => ['CRITICAL', 'BLOCKER'].includes((i.severity || '').toUpperCase())).length}
                </div>
                <div className="metric-label">Critical / Blocker</div>
              </div>
            </div>

            {Object.entries(groupIssuesByFile()).map(([filePath, fileIssues]) => (
              <FileGroup
                key={filePath}
                filePath={filePath}
                issues={fileIssues}
                ruleCache={ruleCache}
                getSeverityBadge={getSeverityBadge}
                getTypeIcon={getTypeIcon}
              />
            ))}

            <div style={{ marginTop: '2rem' }}>
              <button className="btn btn-primary" onClick={runFix}>
                🛠 Auto-Fix These Issues
              </button>
            </div>
          </div>
        )}

        {/* FIXING PROGRESS */}
        {stage === 'fixing' && (
          <div className="progress-container fade-in">
            <h2 style={{ fontSize: '1.3rem', marginBottom: '0.5rem' }}>🤖 Agent Swarm Active</h2>
            <p style={{ color: 'var(--text-secondary)', marginBottom: '1rem' }}>
              LangGraph workers are analysing and patching your code.
            </p>
            <div className="progress-bar-track">
              <div className="progress-bar-fill" style={{ width: `${progress}%` }}></div>
            </div>
            <div className="progress-steps">
              {progressSteps.map((step, i) => (
                <div key={i} className={`progress-step ${step.status === 'complete' ? 'done' : 'done'}`}>
                  <div className="step-icon">✓</div>
                  <span>{step.message}</span>
                </div>
              ))}
            </div>
          </div>
        )}

        {/* REVIEW (HITL) */}
        {stage === 'review' && report && (
          <div className="fade-in">
            <div className="status-banner warning">
              ⚠️ Human approval required — Review every fix below before committing.
            </div>

            <div className="section-header">
              <h2>📊 Fix Review & Approval</h2>
              <p>Inspect each fix, reject any you disagree with, then finalize or abort.</p>
            </div>

            <div className="metrics-grid">
              <div className="metric-card">
                <div className="metric-value">{report.total_fixes_attempted || 0}</div>
                <div className="metric-label">Fixes Attempted</div>
              </div>
              <div className="metric-card">
                <div className="metric-value" style={{ color: 'var(--green)' }}>{report.successful_fixes || 0}</div>
                <div className="metric-label">Successful</div>
              </div>
              <div className="metric-card">
                <div className="metric-value" style={{ color: 'var(--yellow)' }}>{report.remaining_issues || 0}</div>
                <div className="metric-label">Remaining</div>
              </div>
            </div>

            {(report.fixes || []).filter(f => f.status === 'success').map((fix, i) => (
              <FixCard
                key={i}
                fix={fix}
                rejected={rejections.has(fix.file_path)}
                onReject={() => rejectFix(fix.file_path)}
              />
            ))}

            <div className="action-bar">
              <button className="btn btn-primary" onClick={finalize} disabled={loading}>
                {loading ? '⏳ Pushing...' : '✅ Finalize & Push'}
              </button>
              <button className="btn btn-danger" onClick={abort}>
                ❌ Abort All
              </button>
            </div>
          </div>
        )}

        {/* FINALIZED */}
        {stage === 'finalized' && finalizeResult && (
          <div className="finalize-container fade-in">
            <div style={{ fontSize: '4rem', marginBottom: '1rem' }}>🎉</div>
            <h2>Fixes Deployed!</h2>
            <p style={{ color: 'var(--text-secondary)', marginBottom: '1.5rem' }}>
              AutoPatch pushed all approved fixes to branch <code style={{ color: 'var(--accent)' }}>{branch}</code>
            </p>

            {finalizeResult.pr_link && (
              <a href={finalizeResult.pr_link} target="_blank" rel="noreferrer" className="pr-link">
                🔗 Open Pull Request on GitHub
              </a>
            )}

            <div style={{ display: 'flex', gap: '1rem', justifyContent: 'center', marginTop: '2rem' }}>
              <button className="btn btn-ghost btn-sm" onClick={downloadReport}>
                📥 Download Report
              </button>
              <button className="btn btn-ghost btn-sm" onClick={resetAll}>
                🔄 New Scan
              </button>
            </div>
          </div>
        )}
      </main>
    </div>
  )
}


// ═══════════════════════════════════════════════════════
//  Sub-Components
// ═══════════════════════════════════════════════════════

function FileGroup({ filePath, issues, ruleCache, getSeverityBadge, getTypeIcon }) {
  const [open, setOpen] = useState(true)

  return (
    <div className="file-group slide-in">
      <div className="file-group-header" onClick={() => setOpen(!open)}>
        <span className="file-icon">📄</span>
        <span className="file-name">{filePath}</span>
        <span className="issue-count">{issues.length} issue{issues.length !== 1 ? 's' : ''}</span>
        <span style={{ color: 'var(--text-muted)', fontSize: '0.8rem' }}>{open ? '▼' : '▶'}</span>
      </div>
      {open && (
        <div className="file-group-body">
          {issues.map((issue, i) => {
            const rule = ruleCache[issue.rule] || {}
            return (
              <div key={i} className="issue-card">
                <div className="issue-header">
                  <span className={`badge badge-${getSeverityBadge(issue.severity)}`}>
                    {(issue.severity || 'unknown').toUpperCase()}
                  </span>
                  <span className={`badge badge-${(issue.issue_type || '').toLowerCase().replace('_','-')}`}>
                    {getTypeIcon(issue.issue_type)} {(issue.issue_type || '').replace('_', ' ')}
                  </span>
                  <span className="line-num">Line {issue.line || '?'}</span>
                  <code style={{ fontSize: '0.7rem', color: 'var(--text-muted)' }}>{issue.rule}</code>
                </div>

                <div className="issue-message">
                  <strong>Issue:</strong> {issue.message || 'No message'}
                </div>

                {rule.description && (
                  <div className="issue-explanation">
                    <strong>Why is this a problem?</strong>
                    <p style={{ marginTop: '0.25rem' }}>{rule.description.slice(0, 400)}{rule.description.length > 400 ? '...' : ''}</p>
                  </div>
                )}

                {issue.effort && (
                  <div className="issue-effort">⏱ Estimated effort: <strong>{issue.effort}</strong></div>
                )}
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
        <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
          <h3>{fix.file_path}</h3>
          {rejected && <span className="badge badge-rejected">Rejected</span>}
          {fix.flagged_by_judge && !rejected && <span className="badge badge-flagged">⚠️ Flagged</span>}
          {!rejected && !fix.flagged_by_judge && <span className="badge badge-approved">Pending Approval</span>}
        </div>
        <span style={{ color: 'var(--text-muted)', fontSize: '0.8rem' }}>{open ? '▼' : '▶'}</span>
      </div>

      {open && (
        <div className="fix-card-body">
          {fix.flagged_by_judge && (
            <div className="status-banner warning" style={{ marginBottom: '1rem' }}>
              ⚠️ <strong>Judge Warning:</strong> {fix.judge_rationale || 'Business logic may have been altered.'}
            </div>
          )}

          {details.map((fd, i) => (
            <div key={i} className="fix-detail">
              <div className="fix-detail-title">
                <span className={`badge badge-${(fd.severity || '').toLowerCase().replace(' ', '-')}`}>
                  {fd.severity}
                </span>
                <span>Fix {i + 1}: {fd.issue_title}</span>
                <code style={{ fontSize: '0.7rem', color: 'var(--text-muted)' }}>{fd.rule_id}</code>
              </div>

              <div className="fix-section">
                <div className="fix-section-label">🔍 Root Cause</div>
                <div className="fix-section-content fix-root-cause">{fd.root_cause}</div>
              </div>

              <div className="snippets-grid">
                <div className="snippet-box before">
                  <div className="snippet-label">❌ Before</div>
                  <pre>{fd.original_snippet}</pre>
                </div>
                <div className="snippet-box after">
                  <div className="snippet-label">✅ After</div>
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
                {diffOpen ? '▼' : '▶'} Full Unified Diff
              </div>
              {diffOpen && <pre>{fix.diff_data.diff}</pre>}
            </div>
          )}

          {!rejected && (
            <button className="btn btn-danger btn-sm" style={{ marginTop: '1rem' }} onClick={onReject}>
              ❌ Reject This Fix
            </button>
          )}

          {rejected && (
            <div className="status-banner error" style={{ marginTop: '1rem' }}>
              This fix was rejected and reverted.
            </div>
          )}
        </div>
      )}
    </div>
  )
}


export default App
