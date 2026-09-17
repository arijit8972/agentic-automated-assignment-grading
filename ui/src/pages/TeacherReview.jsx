import { useState, useEffect } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import { fetchJson } from '../lib/api'

export default function TeacherReview() {
  const { subject, rollNo, filename } = useParams()
  const navigate = useNavigate()
  const [submission, setSubmission] = useState(null)
  const [scores, setScores] = useState({})
  const [feedback, setFeedback] = useState('')
  const [saving, setSaving] = useState(false)

  useEffect(() => {
    fetchJson(`/api/teacher/submissions/${subject}/${rollNo}/${filename}`)
      .then(data => {
        setSubmission(data)
        const initial = {}
        ;(data.questions || []).forEach(q => { initial[q.number] = q.score })
        setScores(initial)
        setFeedback(data.ai_feedback || '')
      })
      .catch(() => setSubmission({ error: 'Unable to load submission.' }))
  }, [subject, rollNo, filename])

  const handleScoreChange = (qNum, value) => {
    setScores(prev => ({ ...prev, [qNum]: parseFloat(value) || 0 }))
  }

  const handleApprove = async () => {
    setSaving(true)
    await fetchJson(`/api/teacher/submissions/${subject}/${rollNo}/${filename}/approve`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ scores, feedback }),
    })
    setSaving(false)
    navigate('/teacher')
  }

  if (!submission) return <p>Loading...</p>
  if (submission.error) return <p>{submission.error}</p>

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '1.5rem' }}>
        <h2>Review: {submission.student_name}</h2>
        <span className="badge badge-warning">Needs Review</span>
      </div>

      <div className="review-grid">
        {/* Left: Submission & AI scores */}
        <div>
          <div className="review-panel">
            <h3 style={{ marginBottom: '1rem' }}>Submission Details</h3>
            <p><strong>File:</strong> {submission.filename}</p>
            <p><strong>Subject:</strong> <span style={{ textTransform: 'capitalize' }}>{submission.subject}</span></p>
            <p><strong>Roll No:</strong> {submission.roll_no}</p>
            <p><strong>Submitted:</strong> {submission.submitted_at}</p>
            <div style={{ marginTop: '0.75rem' }}>
              <a
                href={`/api/submission/${subject}/${rollNo}/${submission.filename}/download`}
                className="btn btn-primary"
                style={{ textDecoration: 'none', fontSize: '0.8rem' }}
                target="_blank"
                rel="noopener noreferrer"
              >
                📋 View Answer Sheet
              </a>
            </div>
            <p style={{ marginTop: '0.5rem', color: '#718096', fontSize: '0.85rem' }}>
              Questions flagged by AI are highlighted below.
            </p>
          </div>

          <div className="review-panel" style={{ marginTop: '1.5rem' }}>
            <h3 style={{ marginBottom: '1rem' }}>Question Scores</h3>
            {(submission.questions || []).map(q => (
              <div className="question-row" key={q.number} style={{
                background: q.flagged ? '#fffbeb' : 'transparent',
                borderRadius: '8px',
                padding: '0.75rem',
              }}>
                <div className="question-info">
                  <p style={{ fontWeight: 600 }}>
                    Q{q.number}
                    {q.flagged && <span style={{ color: '#d69e2e', marginLeft: '0.5rem' }}>⚠️ Flagged</span>}
                  </p>
                  <p style={{ color: '#718096', fontSize: '0.85rem' }}>{q.text}</p>
                  <p style={{ color: '#4a5568', fontSize: '0.85rem', marginTop: '0.25rem' }}>
                    <em>Student answer:</em> {q.answer}
                  </p>
                  {q.reasoning && (
                    <p style={{ color: '#718096', fontSize: '0.8rem', marginTop: '0.25rem' }}>
                      <em>AI reasoning:</em> {q.reasoning}
                    </p>
                  )}
                </div>
                <div style={{ display: 'flex', alignItems: 'center', gap: '0.25rem' }}>
                  <input
                    type="number"
                    className="score-input"
                    value={scores[q.number] ?? 0}
                    min={0}
                    max={q.max_score}
                    step={0.5}
                    onChange={(e) => handleScoreChange(q.number, e.target.value)}
                  />
                  <span style={{ color: '#718096' }}>/ {q.max_score}</span>
                </div>
              </div>
            ))}
          </div>
        </div>

        {/* Right: Feedback & Actions */}
        <div>
          <div className="review-panel">
            <h3 style={{ marginBottom: '1rem' }}>Teacher Feedback</h3>
            <textarea
              className="feedback-textarea"
              value={feedback}
              onChange={(e) => setFeedback(e.target.value)}
              placeholder="Add feedback for the student..."
            />
          </div>

          <div className="review-panel" style={{ marginTop: '1.5rem' }}>
            <h3 style={{ marginBottom: '1rem' }}>Summary</h3>
            <p style={{ fontSize: '1.5rem', fontWeight: 700 }}>
              {Object.values(scores).reduce((a, b) => a + b, 0)} / {submission.max_score}
            </p>
            <p style={{ color: '#718096', marginBottom: '1.5rem' }}>Total Score</p>

            <button
              className="btn btn-success"
              onClick={handleApprove}
              disabled={saving}
              style={{ width: '100%' }}
            >
              {saving ? 'Saving...' : 'Approve & Release to Student'}
            </button>
          </div>
        </div>
      </div>
    </div>
  )
}
