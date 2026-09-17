import { useState, useEffect } from 'react'
import { fetchJson } from '../lib/api'
import Markdown from 'react-markdown'


function ReportViewer({ subject, rollNo, filename }) {
  const [markdown, setMarkdown] = useState(null)
  const [feedback, setFeedback] = useState(null)
  const [expanded, setExpanded] = useState(false)
  const [loaded, setLoaded] = useState(false)

  useEffect(() => {
    const loadContent = async () => {
      try {
        const data = await fetchJson(`/api/report/${subject}/${rollNo}/${filename}/content`)
        let md = data.markdown.replace(/^- Source:.*$/m, '').trim()

        // Extract Student Feedback section
        const feedbackMatch = md.match(/## Student Feedback\n\n([\s\S]*?)(?=\n## |$)/)
        if (feedbackMatch) {
          setFeedback(feedbackMatch[1].trim())
          md = md.replace(/## Student Feedback\n\n[\s\S]*?(?=\n## |$)/, '').trim()
        }

        setMarkdown(md)
        setLoaded(true)
      } catch (err) {
        console.error('Failed to load report:', err)
      }
    }
    loadContent()
  }, [subject, rollNo, filename])

  return (
    <div style={{ marginTop: '1rem' }}>
      {feedback && (
        <div style={{ marginBottom: '1rem', padding: '1rem', background: '#f0fdf4', border: '1px solid #bbf7d0', borderRadius: '8px' }}>
          <h4 style={{ marginBottom: '0.5rem', color: '#166534' }}>Student Feedback</h4>
          <div style={{ color: '#4a5568', fontSize: '0.9rem', lineHeight: '1.6' }}>
            <Markdown>{feedback}</Markdown>
          </div>
        </div>
      )}
      {loaded && (
        <button
          className="btn btn-primary"
          onClick={() => setExpanded(!expanded)}
          style={{ fontSize: '0.85rem', marginBottom: expanded ? '1rem' : 0 }}
        >
          {expanded ? '\u25bc Hide Full Report' : '\u25b6 View Full Report'}
        </button>
      )}
      {expanded && markdown && (
        <div className="markdown-report" style={{
          marginTop: '1rem',
          padding: '1.5rem',
          background: 'white',
          border: '1px solid #e2e8f0',
          borderRadius: '8px',
          fontSize: '0.9rem',
          lineHeight: '1.6',
          maxHeight: '600px',
          overflowY: 'auto',
        }}>
          <Markdown>{markdown}</Markdown>
        </div>
      )}
    </div>
  )
}

export default function StudentResults() {
  const [submissions, setSubmissions] = useState([])
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    let active = true
    let intervalRef = null

    const load = async () => {
      try {
        const data = await fetchJson('/api/student/results')
        if (!active) return
        setSubmissions(data.submissions || [])
      } finally {
        if (active) setLoading(false)
      }
    }

    load()
    intervalRef = setInterval(load, 5000)

    return () => {
      active = false
      if (intervalRef) clearInterval(intervalRef)
    }
  }, [])

  if (loading) return <p>Loading results...</p>

  if (!loading && submissions.length === 0) {
    return (
      <div className="card">
        <h2>Results</h2>
        <p style={{ color: '#4a5568' }}>No submissions yet. Upload an assignment to get started.</p>
      </div>
    )
  }

  return (
    <div>
      <h2 style={{ marginBottom: '1.5rem' }}>Results</h2>
      {submissions.map(sub => (
        <div className="card" key={sub.id}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '1rem' }}>
            <div>
              <h3 style={{ marginBottom: '0.25rem' }}>Roll No: {sub.roll_no} &mdash; <span style={{ textTransform: 'capitalize' }}>{sub.subject}</span></h3>
              <p style={{ color: '#718096', fontSize: '0.85rem' }}>Submitted: {sub.submitted_at}</p>
            </div>
            {sub.status === 'graded' ? (
              <span className="badge badge-success">Graded</span>
            ) : (
              <span className="badge badge-warning">Pending Review</span>
            )}
          </div>

          {sub.status === 'graded' && (
            <div>
              <div style={{ display: 'flex', alignItems: 'center', gap: '1.5rem', marginBottom: '1rem' }}>
                <div className={`score-circle ${
                  sub.score_percent >= 80 ? 'score-high' :
                  sub.score_percent >= 50 ? 'score-mid' : 'score-low'
                }`}>
                  {sub.score_percent}%
                </div>
                <div>
                  <p style={{ fontWeight: 700, fontSize: '1.1rem' }}>{sub.total_score}/{sub.max_score}</p>
                </div>
              </div>
              <ReportViewer subject={sub.subject} rollNo={sub.roll_no} filename={sub.filename} />
            </div>
          )}

          {sub.status === 'pending_review' && (
            <div>
              <p style={{ color: '#718096' }}>
                ⏳ Some questions need manual review. Score will be available after teacher review.
              </p>
              {sub.workflow_state && (
                <p style={{ color: '#718096', fontSize: '0.85rem', marginTop: '0.35rem' }}>
                  Pipeline state: {sub.workflow_state}
                </p>
              )}
              {sub.status_message && (
                <div style={{ marginTop: '0.65rem', padding: '0.7rem 0.85rem', background: '#fff7ed', borderRadius: '8px' }}>
                  <p style={{ color: '#7c2d12', fontSize: '0.9rem' }}>{sub.status_message}</p>
                </div>
              )}
            </div>
          )}

          {sub.status === 'graded' && (
            <div style={{ marginTop: '1rem', display: 'flex', gap: '0.75rem' }}>
              <a
                href={`/api/report/${sub.subject}/${sub.roll_no}/${sub.filename}/pdf`}
                className="btn btn-primary"
                style={{ textDecoration: 'none', fontSize: '0.85rem' }}
                download
              >
                📥 Download Report (PDF)
              </a>
              
            </div>
          )}
        </div>
      ))}
    </div>
  )
}
