import { useState, useEffect, useCallback } from 'react'
import './App.css'

// ── Types ──
interface Skill {
  name: string
  description: string
  has_run_script: boolean
  has_skill_md: boolean
}

interface SkillFolder {
  folder: string
  path: string
  has_agents_md: boolean
  skills: Skill[]
}

interface Service {
  name: string
  status: 'running' | 'stopped' | 'error'
  pid: number | null
  topic: string | null
  skills_dir: string | null
  matcher: string
  log_file: string | null
  started_at: number | null
}

interface Config {
  project_root: string
  skills_dir: string
  proxy_binary: string
  server_binary: string
  proxy_exists: boolean
  server_exists: boolean
  python: string
  llm_provider: string
  llm_model: string
}

const API = 'http://localhost:8401'

function StatusBadge({ status }: { status: string }) {
  const colors: Record<string, string> = {
    running: '#22c55e',
    stopped: '#94a3b8',
    error: '#ef4444',
  }
  return (
    <span
      style={{
        display: 'inline-block',
        padding: '2px 10px',
        borderRadius: 12,
        fontSize: 12,
        fontWeight: 600,
        color: '#fff',
        background: colors[status] || '#94a3b8',
      }}
    >
      {status}
    </span>
  )
}

function App() {
  const [folders, setFolders] = useState<SkillFolder[]>([])
  const [services, setServices] = useState<Service[]>([])
  const [config, setConfig] = useState<Config | null>(null)
  const [logLines, setLogLines] = useState<string[]>([])
  const [activeLog, setActiveLog] = useState<string | null>(null)
  const [loading, setLoading] = useState<Record<string, boolean>>({})

  const fetchAll = useCallback(async () => {
    try {
      const [foldersRes, servicesRes, configRes] = await Promise.all([
        fetch(`${API}/api/skills`),
        fetch(`${API}/api/services`),
        fetch(`${API}/api/config`),
      ])
      const foldersData = await foldersRes.json()
      const servicesData = await servicesRes.json()
      const configData = await configRes.json()
      setFolders(foldersData.folders || [])
      setServices(servicesData.services || [])
      setConfig(configData)
    } catch (e) {
      console.error('Failed to fetch data:', e)
    }
  }, [])

  useEffect(() => {
    fetchAll()
    const interval = setInterval(fetchAll, 3000)
    return () => clearInterval(interval)
  }, [fetchAll])

  const setActionLoading = (key: string, value: boolean) =>
    setLoading((prev) => ({ ...prev, [key]: value }))

  const launchProxy = async () => {
    setActionLoading('proxy', true)
    try {
      await fetch(`${API}/api/proxy/launch`, { method: 'POST' })
      await fetchAll()
    } finally {
      setActionLoading('proxy', false)
    }
  }

  const launchServer = async (folder: SkillFolder) => {
    const topicMap: Record<string, string> = {
      'data-processing': 'TOPIC_DATA_PROCESSING',
      'code-analysis': 'TOPIC_CODE_ANALYSIS',
    }
    const topic = topicMap[folder.folder] || `TOPIC_${folder.folder.toUpperCase().replace(/-/g, '_')}`
    setActionLoading(folder.folder, true)
    try {
      await fetch(`${API}/api/server/launch`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          topic,
          skills_dir: folder.path,
          matcher: 'llm',
          description: `${folder.folder} skill server`,
        }),
      })
      await fetchAll()
    } finally {
      setActionLoading(folder.folder, false)
    }
  }

  const stopService = async (name: string) => {
    setActionLoading(name, true)
    try {
      await fetch(`${API}/api/services/${name}/stop`, { method: 'POST' })
      await fetchAll()
    } finally {
      setActionLoading(name, false)
    }
  }

  const restartService = async (name: string) => {
    setActionLoading(name, true)
    try {
      await fetch(`${API}/api/services/${name}/restart`, { method: 'POST' })
      await fetchAll()
    } finally {
      setActionLoading(name, false)
    }
  }

  const launchAll = async () => {
    setActionLoading('all', true)
    try {
      await fetch(`${API}/api/launch-all`, { method: 'POST' })
      await fetchAll()
    } finally {
      setActionLoading('all', false)
    }
  }

  const stopAll = async () => {
    setActionLoading('all', true)
    try {
      await fetch(`${API}/api/stop-all`, { method: 'POST' })
      await fetchAll()
    } finally {
      setActionLoading('all', false)
    }
  }

  const viewLogs = async (name: string) => {
    setActiveLog(name)
    try {
      const res = await fetch(`${API}/api/services/${name}/logs?tail=300`)
      const data = await res.json()
      setLogLines(data.lines || [])
    } catch {
      setLogLines(['Failed to load logs'])
    }
  }

  const getServiceForFolder = (folder: string): Service | undefined =>
    services.find((s) => s.name === `server-${folder}` || s.skills_dir?.includes(folder))

  const proxyService = services.find((s) => s.name === 'proxy')

  return (
    <div className="app">
      <header className="header">
        <div className="header-left">
          <h1>SkillScale Management</h1>
          {config && (
            <span className="config-badge">
              {config.llm_provider} / {config.llm_model}
            </span>
          )}
        </div>
        <div className="header-actions">
          <button className="btn btn-primary" onClick={launchAll} disabled={loading['all']}>
            {loading['all'] ? '...' : 'Launch All'}
          </button>
          <button className="btn btn-danger" onClick={stopAll} disabled={loading['all']}>
            {loading['all'] ? '...' : 'Stop All'}
          </button>
        </div>
      </header>

      <div className="content">
        <section className="section">
          <h2>Proxy</h2>
          <div className="card proxy-card">
            <div className="card-header">
              <div>
                <strong>XPUB/XSUB Proxy</strong>
                <p className="card-subtitle">ZeroMQ message broker</p>
              </div>
              <StatusBadge status={proxyService?.status || 'stopped'} />
            </div>
            <div className="card-actions">
              {proxyService?.status === 'running' ? (
                <>
                  <button className="btn btn-sm btn-danger" onClick={() => stopService('proxy')}>Stop</button>
                  <button className="btn btn-sm btn-secondary" onClick={() => restartService('proxy')}>Restart</button>
                  <button className="btn btn-sm btn-secondary" onClick={() => viewLogs('proxy')}>Logs</button>
                </>
              ) : (
                <button className="btn btn-sm btn-primary" onClick={launchProxy} disabled={loading['proxy']}>
                  {loading['proxy'] ? 'Starting...' : 'Launch'}
                </button>
              )}
              {proxyService?.pid && <span className="pid-label">PID: {proxyService.pid}</span>}
            </div>
          </div>
        </section>

        <section className="section">
          <h2>Skill Servers</h2>
          <div className="cards-grid">
            {folders.map((folder) => {
              const svc = getServiceForFolder(folder.folder)
              return (
                <div key={folder.folder} className="card">
                  <div className="card-header">
                    <div>
                      <strong>{folder.folder}</strong>
                      <p className="card-subtitle">
                        {folder.skills.length} skill{folder.skills.length !== 1 ? 's' : ''}{' '}
                        {folder.has_agents_md && '• AGENTS.md'}
                      </p>
                    </div>
                    <StatusBadge status={svc?.status || 'stopped'} />
                  </div>
                  <div className="skills-list">
                    {folder.skills.map((skill) => (
                      <div key={skill.name} className="skill-item">
                        <span className="skill-name">{skill.name}</span>
                        <span className="skill-desc">{skill.description}</span>
                      </div>
                    ))}
                  </div>
                  <div className="card-actions">
                    {svc?.status === 'running' ? (
                      <>
                        <button className="btn btn-sm btn-danger" onClick={() => stopService(svc.name)}>Stop</button>
                        <button className="btn btn-sm btn-secondary" onClick={() => restartService(svc.name)}>Restart</button>
                        <button className="btn btn-sm btn-secondary" onClick={() => viewLogs(svc.name)}>Logs</button>
                      </>
                    ) : (
                      <button className="btn btn-sm btn-primary" onClick={() => launchServer(folder)} disabled={loading[folder.folder]}>
                        {loading[folder.folder] ? 'Starting...' : 'Launch'}
                      </button>
                    )}
                    {svc?.pid && <span className="pid-label">PID: {svc.pid}</span>}
                  </div>
                </div>
              )
            })}
          </div>
        </section>

        {activeLog && (
          <section className="section">
            <div className="log-header">
              <h2>Logs — {activeLog}</h2>
              <div>
                <button className="btn btn-sm btn-secondary" onClick={() => viewLogs(activeLog)}>Refresh</button>
                <button className="btn btn-sm btn-secondary" onClick={() => { setActiveLog(null); setLogLines([]) }}>Close</button>
              </div>
            </div>
            <pre className="log-viewer">
              {logLines.length > 0 ? logLines.join('\n') : 'No log output yet.'}
            </pre>
          </section>
        )}

        {config && (
          <section className="section">
            <h2>System Configuration</h2>
            <div className="config-grid">
              <div className="config-item"><label>Project Root</label><span>{config.project_root}</span></div>
              <div className="config-item"><label>Python</label><span>{config.python}</span></div>
              <div className="config-item"><label>LLM Provider</label><span>{config.llm_provider}</span></div>
              <div className="config-item"><label>LLM Model</label><span>{config.llm_model}</span></div>
              <div className="config-item"><label>Proxy Binary</label><span>{config.proxy_exists ? '✓' : '✗'} {config.proxy_binary}</span></div>
              <div className="config-item"><label>Server Binary</label><span>{config.server_exists ? '✓' : '✗'} {config.server_binary}</span></div>
            </div>
          </section>
        )}
      </div>
    </div>
  )
}

export default App