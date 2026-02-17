import { useState, useEffect, useRef, useCallback } from 'react'
import './App.css'

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
  timestamp: number
}

const API = 'http://localhost:8402'

function App() {
  const [messages, setMessages] = useState<Message[]>([])
  const [input, setInput] = useState('')
  const [topics, setTopics] = useState<Topic[]>([])
  const [selectedTopic, setSelectedTopic] = useState<string>('')
  const [loading, setLoading] = useState(false)
  const [provider, setProvider] = useState('')
  const [model, setModel] = useState('')
  const messagesEndRef = useRef<HTMLDivElement>(null)
  const inputRef = useRef<HTMLTextAreaElement>(null)

  const scrollToBottom = useCallback(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [])

  useEffect(() => {
    scrollToBottom()
  }, [messages, scrollToBottom])

  useEffect(() => {
    fetch(`${API}/api/config`)
      .then((r) => r.json())
      .then((data) => {
        setTopics(data.topics || [])
        setProvider(data.llm_provider || '')
        setModel(data.llm_model || '')
      })
      .catch(() => {})
  }, [])

  const sendMessage = async () => {
    const text = input.trim()
    if (!text || loading) return

    const userMsg: Message = {
      id: crypto.randomUUID().slice(0, 12),
      role: 'user',
      content: text,
      timestamp: Date.now(),
    }
    setMessages((prev) => [...prev, userMsg])
    setInput('')
    setLoading(true)

    try {
      const res = await fetch(`${API}/api/chat`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          message: text,
          topic: selectedTopic || undefined,
        }),
      })

      if (!res.ok) {
        const err = await res.json().catch(() => ({ detail: 'Unknown error' }))
        const errMsg: Message = {
          id: crypto.randomUUID().slice(0, 12),
          role: 'system',
          content: `Error: ${err.detail || res.statusText}`,
          timestamp: Date.now(),
        }
        setMessages((prev) => [...prev, errMsg])
        return
      }

      const data = await res.json()
      const assistantMsg: Message = {
        id: data.id,
        role: 'assistant',
        content: data.message,
        topic: data.topic,
        elapsed_ms: data.elapsed_ms,
        routed_by: data.routed_by,
        timestamp: Date.now(),
      }
      setMessages((prev) => [...prev, assistantMsg])
    } catch (err) {
      const errMsg: Message = {
        id: crypto.randomUUID().slice(0, 12),
        role: 'system',
        content: `Connection error: Is the chat server running on ${API}?`,
        timestamp: Date.now(),
      }
      setMessages((prev) => [...prev, errMsg])
    } finally {
      setLoading(false)
      inputRef.current?.focus()
    }
  }

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      sendMessage()
    }
  }

  const clearChat = () => {
    setMessages([])
  }

  return (
    <div className="chat-app">
      {/* Sidebar */}
      <aside className="sidebar">
        <div className="sidebar-header">
          <h2>SkillScale Chat</h2>
          {provider && (
            <div className="provider-info">
              <span className="provider-badge">{provider}</span>
              <span className="model-name">{model}</span>
            </div>
          )}
        </div>

        <div className="sidebar-section">
          <h3>Topic Routing</h3>
          <select
            value={selectedTopic}
            onChange={(e) => setSelectedTopic(e.target.value)}
            className="topic-select"
          >
            <option value="">Auto (LLM routing)</option>
            {topics.map((t) => (
              <option key={t.topic} value={t.topic}>
                {t.topic}
              </option>
            ))}
          </select>
        </div>

        <div className="sidebar-section">
          <h3>Available Skills</h3>
          <div className="topic-list">
            {topics.map((t) => (
              <div key={t.topic} className="topic-group">
                <div className="topic-name">{t.topic}</div>
                {t.skills.map((s) => (
                  <div key={s.name} className="topic-skill">
                    <span className="skill-dot" />
                    {s.name}
                  </div>
                ))}
              </div>
            ))}
          </div>
        </div>

        <div className="sidebar-footer">
          <button className="btn-clear" onClick={clearChat}>
            Clear Chat
          </button>
        </div>
      </aside>

      {/* Chat area */}
      <main className="chat-main">
        <div className="messages-container">
          {messages.length === 0 && (
            <div className="empty-state">
              <h2>SkillScale Chat</h2>
              <p>Send a message to test your skill servers.</p>
              <div className="example-prompts">
                <button onClick={() => setInput('Summarize the following text: Machine learning is a subset of artificial intelligence that focuses on building systems that learn from data. These systems can identify patterns and make decisions with minimal human intervention.')}>
                  Summarize text
                </button>
                <button onClick={() => setInput('Analyze the complexity of this Python function:\ndef fibonacci(n):\n    if n <= 1:\n        return n\n    return fibonacci(n-1) + fibonacci(n-2)')}>
                  Analyze code complexity
                </button>
                <button onClick={() => setInput('Analyze this CSV data:\nname,age,city\nAlice,30,NYC\nBob,25,LA\nCharlie,35,Chicago')}>
                  Analyze CSV data
                </button>
                <button onClick={() => setInput('Find dead code in this Python:\ndef used_func():\n    return 42\n\ndef unused_func():\n    return 0\n\nresult = used_func()')}>
                  Detect dead code
                </button>
              </div>
            </div>
          )}

          {messages.map((msg) => (
            <div key={msg.id} className={`message message-${msg.role}`}>
              <div className="message-header">
                <span className="message-role">
                  {msg.role === 'user' ? 'You' : msg.role === 'assistant' ? 'SkillScale' : 'System'}
                </span>
                {msg.topic && (
                  <span className="message-meta">
                    {msg.topic} · {msg.routed_by} · {msg.elapsed_ms}ms
                  </span>
                )}
              </div>
              <div className="message-body">
                <pre>{msg.content}</pre>
              </div>
            </div>
          ))}

          {loading && (
            <div className="message message-assistant">
              <div className="message-header">
                <span className="message-role">SkillScale</span>
              </div>
              <div className="message-body loading-dots">
                <span />
                <span />
                <span />
              </div>
            </div>
          )}

          <div ref={messagesEndRef} />
        </div>

        {/* Input area */}
        <div className="input-area">
          <textarea
            ref={inputRef}
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder="Type your message... (Enter to send, Shift+Enter for new line)"
            rows={3}
            disabled={loading}
          />
          <button
            className="send-btn"
            onClick={sendMessage}
            disabled={loading || !input.trim()}
          >
            {loading ? '...' : 'Send'}
          </button>
        </div>
      </main>
    </div>
  )
}

export default App