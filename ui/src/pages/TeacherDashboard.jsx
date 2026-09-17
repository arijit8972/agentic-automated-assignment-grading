import { useState, useEffect } from 'react'
import { Link } from 'react-router-dom'
import { fetchJson } from '../lib/api'

export default function TeacherDashboard() {
  const [submissions, setSubmissions] = useState([])
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    let active = true
    let intervalRef = null

    const load = async () => {
      try {
        const data = await fetchJson('/api/teacher/submissions')
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

  if (loading) return <p>Loading submissions...</p>

  const needsReview = submissions.filter(s => s.status === 'needs_review')

  return (
    <div>
      <h2 style={{ marginBottom: '1.5rem' }}>Teacher Dashboard</h2>

      {needsReview.length > 0 && (
        <div className="card">
          <h3 style={{ marginBottom: '1rem', color: '#c05621' }}>
            ⚠️ Needs Manual Review ({needsReview.length})
          </h3>
          <table className="submission-table">
            <thead>
              <tr>
                <th>Roll No</th>
                <th>Subject</th>
                <th>Assignment</th>
                <th>Flagged Questions</th>
                <th>Action</th>
              </tr>
            </thead>
            <tbody>
              {needsReview.map(sub => (
                <tr key={sub.id}>
                  <td>{sub.roll_no}</td>
                  <td style={{ textTransform: 'capitalize' }}>{sub.subject}</td>
                  <td>{sub.filename}</td>
                  <td>
                    {sub.flagged_questions.map(q => (
                      <span key={q} className="badge badge-warning" style={{ marginRight: '0.25rem' }}>
                        Q{q}
                      </span>
                    ))}
                  </td>
                  <td>
                    <Link to={`/teacher/review/${sub.subject}/${sub.roll_no}/${sub.filename}`} className="btn btn-warning" style={{ fontSize: '0.8rem', padding: '0.4rem 0.8rem' }}>
                      Review
                    </Link>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <div className="card">
        <h3 style={{ marginBottom: '1rem' }}>All Submissions ({submissions.length})</h3>
        <table className="submission-table">
          <thead>
            <tr>
              <th>Roll No</th>
              <th>Subject</th>
              <th>Assignment</th>
              <th>Score</th>
              <th>Status</th>
              <th>Submitted</th>
            </tr>
          </thead>
          <tbody>
            {submissions.map(sub => (
              <tr key={sub.id}>
                <td>{sub.roll_no}</td>
                <td style={{ textTransform: 'capitalize' }}>{sub.subject}</td>
                <td>{sub.filename}</td>
                <td>{sub.status === 'graded' ? `${sub.total_score}/${sub.max_score}` : '—'}</td>
                <td>
                  <span className={`badge ${sub.status === 'graded' ? 'badge-success' : 'badge-warning'}`}>
                    {sub.status === 'graded' ? 'Graded' : 'Needs Review'}
                  </span>
                </td>
                <td>{sub.submitted_at}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}
