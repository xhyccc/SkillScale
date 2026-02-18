import { useState, useEffect, useCallback, useRef } from 'react'
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
  docker?: boolean
  docker_service?: string
  docker_health?: string | null
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
  docker_mode?: boolean
}

interface TopicSkill {
  name: string
  description: string
}

interface Topic {
  topic: string
  description: string
  skills: TopicSkill[]
}

interface Message {
  id: string
  role: 'user' | 'assistant' | 'system'
  content: string
  topic?: string
  elapsed_ms?: number
  routed_by?: string
  trace_id?: string
  timestamp: number
}

interface TraceSpan {
  name: string
  phase: string
  start_ms: number
  end_ms: number
  duration_ms: number
  details: Record<string, unknown>
}

interface Trace {
  trace_id: string
  message: string
  topic: string
  result: string
  status: string
  total_ms: number
  created_at: number
  spans: TraceSpan[]
}

const API = '/api'

type Tab = 'dashboard' | 'chat' | 'traces'

const PHASE_COLORS: Record<string, string> = {
  routing: '#f59e0b',
  zmq: '#3b82f6',
  skill_server: '#8b5cf6',
  skill_exec: '#22c55e',
}

const PHASE_LABELS: Record<string, string> = {
  routing: 'Topic Routing',
  zmq: 'ZeroMQ Transport',
  skill_server: 'Skill Server',
  skill_exec: 'Skill Execution',
}

// ── Shared Components ──
function StatusBadge({ status }: { status: string }) {
  const colors: Record<string, string> = {
    running: '#22c55e',
    stopped: '#94a3b8',
    error: '#ef4444',
    success: '#22c55e',
    pending: '#f59e0b',
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

function TabButton({ active, label, onClick }: { active: boolean; label: string; onClick: () => void }) {
  return (
    <button
      className={`tab-btn ${active ? 'tab-btn-active' : ''}`}
      onClick={onClick}
    >
      {label}
    </button>
  )
}

// ── Trace Viewer Component ──
function TraceViewer({ trace }: { trace: Trace }) {
  const [expanded, setExpanded] = useState<Record<number, boolean>>({})

  const toggleExpand = (idx: number) => {
    setExpanded(prev => ({ ...prev, [idx]: !prev[idx] }))
  }

  return (
    <div className="trace-viewer">
      <div className="trace-header-info">
        <div className="trace-meta">
          <StatusBadge status={trace.status} />
          <span className="trace-topic">{trace.topic}</span>
          <span className="trace-timing">{trace.total_ms.toFixed(0)}ms total</span>
        </div>
        <div className="trace-message-preview">{trace.message}</div>
      </div>

      <div className="trace-waterfall">
        <h4>Request Waterfall</h4>
        {trace.spans.map((span, idx) => {
          const color = PHASE_COLORS[span.phase] || '#94a3b8'
          const maxMs = Math.max(trace.total_ms, 1)
          const widthPct = Math.max(((span.duration_ms || 0) / maxMs) * 100, 3)

          return (
            <div key={idx} className="waterfall-row" onClick={() => toggleExpand(idx)}>
              <div className="waterfall-label">
                <span className="waterfall-phase" style={{ color }}>
                  {PHASE_LABELS[span.phase] || span.phase}
                </span>
                <span className="waterfall-name">{span.name}</span>
              </div>
              <div className="waterfall-bar-container">
                <div
                  className="waterfall-bar"
                  style={{
                    width: `${widthPct}%`,
                    backgroundColor: color,
                  }}
                />
                <span className="waterfall-duration">
                  {span.duration_ms > 0 ? `${span.duration_ms.toFixed(0)}ms` : '—'}
                </span>
              </div>

              {expanded[idx] && (
                <div className="waterfall-details" onClick={e => e.stopPropagation()}>
                  {Object.entries(span.details).map(([key, value]) => {
                    // Render exec_logs as a log console
                    if (key === 'exec_logs' && Array.isArray(value) && value.length > 0) {
                      return (
                        <div key={key} className="detail-row detail-logs">
                          <span className="detail-key">Execution Logs:</span>
                          <pre className="exec-log-block">
                            {(value as string[]).map((line, i) => (
                              <div key={i} className="exec-log-line">{line}</div>
                            ))}
                          </pre>
                        </div>
                      )
                    }
                    // Render stderr with warning styling
                    if (key === 'stderr' && typeof value === 'string' && value.trim()) {
                      return (
                        <div key={key} className="detail-row detail-stderr">
                          <span className="detail-key">stderr:</span>
                          <pre className="stderr-block">{value}</pre>
                        </div>
                      )
                    }
                    return (
                      <div key={key} className="detail-row">
                        <span className="detail-key">{key}:</span>
                        <span className="detail-value">
                          {typeof value === 'object'
                            ? JSON.stringify(value, null, 2)
                            : String(value)}
                        </span>
                      </div>
                    )
                  })}
                </div>
              )}
            </div>
          )
        })}
      </div>

      {trace.result && (
        <div className="trace-result">
          <h4>Skill Output</h4>
          <pre>{trace.result}</pre>
        </div>
      )}
    </div>
  )
}


// ═══════════════════════════════════════════════════
//  Main App
// ═══════════════════════════════════════════════════
function App() {
  const [activeTab, setActiveTab] = useState<Tab>('dashboard')
  const [config, setConfig] = useState<Config | null>(null)

  // Dashboard state
  const [folders, setFolders] = useState<SkillFolder[]>([])
  const [services, setServices] = useState<Service[]>([])
  const [dockerMode, setDockerMode] = useState(false)
  const [logLines, setLogLines] = useState<string[]>([])
  const [activeLog, setActiveLog] = useState<string | null>(null)
  const [loading, setLoading] = useState<Record<string, boolean>>({})

  // Chat state
  const [messages, setMessages] = useState<Message[]>([])
  const [chatInput, setChatInput] = useState('')
  const [chatLoading, setChatLoading] = useState(false)
  const [topics, setTopics] = useState<Topic[]>([])
  const [selectedTopic, setSelectedTopic] = useState('')
  const [activeTrace, setActiveTrace] = useState<Trace | null>(null)
  const messagesEndRef = useRef<HTMLDivElement>(null)
  const inputRef = useRef<HTMLTextAreaElement>(null)

  // Traces state
  const [traces, setTraces] = useState<Trace[]>([])
  const [selectedTrace, setSelectedTrace] = useState<Trace | null>(null)

  // ── Data fetching ──
  const fetchDashboard = useCallback(async () => {
    try {
      const [foldersRes, servicesRes, configRes] = await Promise.all([
        fetch(`${API}/skills`),
        fetch(`${API}/services`),
        fetch(`${API}/config`),
      ])
      setFolders((await foldersRes.json()).folders || [])
      const svcData = await servicesRes.json()
      setServices(svcData.services || [])
      setDockerMode(svcData.docker_mode || false)
      setConfig(await configRes.json())
    } catch (e) {
      console.error('Failed to fetch:', e)
    }
  }, [])

  const fetchTopics = useCallback(async () => {
    try {
      const res = await fetch(`${API}/topics`)
      const data = await res.json()
      setTopics(data.topics || [])
    } catch {}
  }, [])

  const fetchTraces = useCallback(async () => {
    try {
      const res = await fetch(`${API}/traces`)
      const data = await res.json()
      setTraces(data.traces || [])
    } catch {}
  }, [])

  useEffect(() => {
    fetchDashboard()
    fetchTopics()
    const interval = setInterval(fetchDashboard, 3000)
    return () => clearInterval(interval)
  }, [fetchDashboard, fetchTopics])

  useEffect(() => {
    if (activeTab === 'traces') fetchTraces()
  }, [activeTab, fetchTraces])

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages])

  // ── Dashboard actions ──
  const setActionLoading = (key: string, val: boolean) =>
    setLoading(prev => ({ ...prev, [key]: val }))

  const launchProxy = async () => {
    setActionLoading('proxy', true)
    try {
      await fetch(`${API}/proxy/launch`, { method: 'POST' })
      await fetchDashboard()
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
      await fetch(`${API}/server/launch`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ topic, skills_dir: folder.path, matcher: 'llm' }),
      })
      await fetchDashboard()
    } finally {
      setActionLoading(folder.folder, false)
    }
  }

  const stopService = async (name: string) => {
    setActionLoading(name, true)
    try {
      await fetch(`${API}/services/${name}/stop`, { method: 'POST' })
      await fetchDashboard()
    } finally {
      setActionLoading(name, false)
    }
  }

  const restartService = async (name: string) => {
    setActionLoading(name, true)
    try {
      await fetch(`${API}/services/${name}/restart`, { method: 'POST' })
      await fetchDashboard()
    } finally {
      setActionLoading(name, false)
    }
  }

  const launchAll = async () => {
    setActionLoading('all', true)
    try {
      await fetch(`${API}/launch-all`, { method: 'POST' })
      await fetchDashboard()
    } finally {
      setActionLoading('all', false)
    }
  }

  const stopAll = async () => {
    setActionLoading('all', true)
    try {
      await fetch(`${API}/stop-all`, { method: 'POST' })
      await fetchDashboard()
    } finally {
      setActionLoading('all', false)
    }
  }

  const viewLogs = async (name: string) => {
    setActiveLog(name)
    try {
      const res = await fetch(`${API}/services/${name}/logs?tail=300`)
      const data = await res.json()
      setLogLines(data.lines || [])
    } catch {
      setLogLines(['Failed to load logs'])
    }
  }

  const getServiceForFolder = (folder: string): Service | undefined =>
    services.find(s => s.name === `server-${folder}` || s.skills_dir?.includes(folder))

  const proxyService = services.find(s => s.name === 'proxy')

  // ── Chat actions ──
  const sendMessage = async () => {
    const text = chatInput.trim()
    if (!text || chatLoading) return

    const userMsg: Message = {
      id: crypto.randomUUID().slice(0, 12),
      role: 'user',
      content: text,
      timestamp: Date.now(),
    }
    setMessages(prev => [...prev, userMsg])
    setChatInput('')
    setChatLoading(true)
    setActiveTrace(null)

    try {
      const res = await fetch(`${API}/chat`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ message: text, topic: selectedTopic || undefined }),
      })

      if (!res.ok) {
        const err = await res.json().catch(() => ({ detail: 'Unknown error' }))
        setMessages(prev => [...prev, {
          id: crypto.randomUUID().slice(0, 12),
          role: 'system',
          content: `Error: ${err.detail || res.statusText}`,
          timestamp: Date.now(),
        }])
        return
      }

      const data = await res.json()
      setMessages(prev => [...prev, {
        id: data.id,
        role: 'assistant',
        content: data.message,
        topic: data.topic,
        elapsed_ms: data.elapsed_ms,
        routed_by: data.routed_by,
        trace_id: data.trace_id,
        timestamp: Date.now(),
      }])

      // Load the trace for this response
      if (data.trace_id) {
        try {
          const traceRes = await fetch(`${API}/traces/${data.trace_id}`)
          if (traceRes.ok) {
            setActiveTrace(await traceRes.json())
          }
        } catch {}
      }
    } catch {
      setMessages(prev => [...prev, {
        id: crypto.randomUUID().slice(0, 12),
        role: 'system',
        content: 'Connection error: Is the server running?',
        timestamp: Date.now(),
      }])
    } finally {
      setChatLoading(false)
      inputRef.current?.focus()
    }
  }

  const handleChatKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      sendMessage()
    }
  }

  const setExamplePrompt = (text: string) => {
    setChatInput(text)
    inputRef.current?.focus()
  }

  // ── Render ──
  return (
    <div className="app">
      <header className="header">
        <div className="header-left">
          <h1>SkillScale</h1>
          {config && (
            <span className="config-badge">{config.llm_provider} / {config.llm_model}</span>
          )}
          {dockerMode && (
            <span className="config-badge" style={{ background: '#3b82f6' }}>Docker</span>
          )}
        </div>
        <div className="header-tabs">
          <TabButton active={activeTab === 'dashboard'} label="Dashboard" onClick={() => setActiveTab('dashboard')} />
          <TabButton active={activeTab === 'chat'} label="Chat Testing" onClick={() => { setActiveTab('chat'); fetchTopics() }} />
          <TabButton active={activeTab === 'traces'} label="Traces" onClick={() => setActiveTab('traces')} />
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

      {/* ═══ Dashboard Tab ═══ */}
      {activeTab === 'dashboard' && (
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
                {proxyService?.docker && <span className="pid-label" style={{ color: '#3b82f6' }}>🐳 Docker</span>}
              </div>
            </div>
          </section>

          <section className="section">
            <h2>Skill Servers</h2>
            <div className="cards-grid">
              {folders.map(folder => {
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
                      {folder.skills.map(skill => (
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
                      {svc?.docker && <span className="pid-label" style={{ color: '#3b82f6' }}>🐳 Docker</span>}
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
      )}

      {/* ═══ Chat Testing Tab ═══ */}
      {activeTab === 'chat' && (
        <div className="chat-layout">
          <div className="chat-main">
            <div className="chat-messages">
              {messages.length === 0 && (
                <div className="empty-state">
                  <h2>Chat Testing</h2>
                  <p>Send a message to test your skill servers end-to-end.</p>
                  <div className="example-prompts">
                    <button onClick={() => setExamplePrompt('Summarize the following text: Machine learning is a subset of artificial intelligence that focuses on building systems that learn from data.')}>
                      Summarize text
                    </button>
                    <button onClick={() => setExamplePrompt('Analyze the complexity of this Python function:\ndef fibonacci(n):\n    if n <= 1:\n        return n\n    return fibonacci(n-1) + fibonacci(n-2)')}>
                      Code complexity
                    </button>
                    <button onClick={() => setExamplePrompt('Analyze this CSV data:\nname,age,city\nAlice,30,NYC\nBob,25,LA\nCharlie,35,Chicago')}>
                      CSV analysis
                    </button>
                    <button onClick={() => setExamplePrompt('Find dead code in this Python:\ndef used_func():\n    return 42\n\ndef unused_func():\n    return 0\n\nresult = used_func()')}>
                      Dead code detection
                    </button>
                  </div>
                </div>
              )}

              {messages.map(msg => (
                <div key={msg.id} className={`message message-${msg.role}`}>
                  <div className="message-header">
                    <span className="message-role">
                      {msg.role === 'user' ? 'You' : msg.role === 'assistant' ? 'SkillScale' : 'System'}
                    </span>
                    {msg.topic && (
                      <span className="message-meta">
                        {msg.topic} · {msg.routed_by} · {msg.elapsed_ms?.toFixed(0)}ms
                        {msg.trace_id && (
                          <button
                            className="trace-link"
                            onClick={async () => {
                              try {
                                const res = await fetch(`${API}/traces/${msg.trace_id}`)
                                if (res.ok) setActiveTrace(await res.json())
                              } catch {}
                            }}
                          >
                            View Trace
                          </button>
                        )}
                      </span>
                    )}
                  </div>
                  <div className="message-body">
                    <pre>{msg.content}</pre>
                  </div>
                </div>
              ))}

              {chatLoading && (
                <div className="message message-assistant">
                  <div className="message-header">
                    <span className="message-role">SkillScale</span>
                  </div>
                  <div className="message-body loading-dots">
                    <span /><span /><span />
                  </div>
                </div>
              )}
              <div ref={messagesEndRef} />
            </div>

            <div className="chat-input-area">
              <div className="chat-input-controls">
                <select
                  value={selectedTopic}
                  onChange={e => setSelectedTopic(e.target.value)}
                  className="topic-select"
                >
                  <option value="">Auto (LLM routing)</option>
                  {topics.map(t => (
                    <option key={t.topic} value={t.topic}>{t.topic}</option>
                  ))}
                </select>
                <button className="btn btn-sm btn-secondary" onClick={() => { setMessages([]); setActiveTrace(null) }}>
                  Clear
                </button>
              </div>
              <div className="chat-input-row">
                <textarea
                  ref={inputRef}
                  value={chatInput}
                  onChange={e => setChatInput(e.target.value)}
                  onKeyDown={handleChatKeyDown}
                  placeholder="Type your message... (Enter to send)"
                  rows={3}
                  disabled={chatLoading}
                />
                <button
                  className="btn btn-primary send-btn"
                  onClick={sendMessage}
                  disabled={chatLoading || !chatInput.trim()}
                >
                  {chatLoading ? '...' : 'Send'}
                </button>
              </div>
            </div>
          </div>

          {activeTrace && (
            <div className="chat-trace-panel">
              <div className="trace-panel-header">
                <h3>Request Trace</h3>
                <button className="btn btn-sm btn-secondary" onClick={() => setActiveTrace(null)}>Close</button>
              </div>
              <TraceViewer trace={activeTrace} />
            </div>
          )}
        </div>
      )}

      {/* ═══ Traces Tab ═══ */}
      {activeTab === 'traces' && (
        <div className="traces-layout">
          <div className="traces-list">
            <div className="traces-list-header">
              <h2>Request Traces</h2>
              <button className="btn btn-sm btn-secondary" onClick={fetchTraces}>Refresh</button>
            </div>
            {traces.length === 0 ? (
              <div className="empty-state">
                <p>No traces yet. Send some messages in Chat Testing.</p>
              </div>
            ) : (
              traces.map(t => (
                <div
                  key={t.trace_id}
                  className={`trace-item ${selectedTrace?.trace_id === t.trace_id ? 'trace-item-active' : ''}`}
                  onClick={() => setSelectedTrace(t)}
                >
                  <div className="trace-item-top">
                    <StatusBadge status={t.status} />
                    <span className="trace-item-topic">{t.topic}</span>
                    <span className="trace-item-time">{t.total_ms.toFixed(0)}ms</span>
                  </div>
                  <div className="trace-item-message">{t.message}</div>
                  <div className="trace-item-date">
                    {new Date(t.created_at * 1000).toLocaleTimeString()}
                    {' · '}{t.spans.length} spans
                  </div>
                </div>
              ))
            )}
          </div>
          <div className="traces-detail">
            {selectedTrace ? (
              <TraceViewer trace={selectedTrace} />
            ) : (
              <div className="empty-state">
                <p>Select a trace to view details.</p>
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  )
}

export default App
