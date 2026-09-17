import { useEffect, useState } from 'react'
import { fetchJson } from '../lib/api'
import Markdown from 'react-markdown'


function UploadReportViewer({ subject, rollNo, filename }) {
  const [markdown, setMarkdown] = useState(null)
  const [feedback, setFeedback] = useState(null)
  const [expanded, setExpanded] = useState(false)

  useEffect(() => {
    if (!subject || !rollNo || !filename) return
    let active = true
    let retryTimer = null

    const load = async () => {
      try {
        const data = await fetchJson(`/api/report/${subject}/${rollNo}/${filename}/content`)
        if (!active) return
        let md = data.markdown.replace(/^- Source:.*$/m, '').trim()
        const feedbackMatch = md.match(/## Student Feedback\n\n([\s\S]*?)(?=\n## |$)/)
        if (feedbackMatch) {
          setFeedback(feedbackMatch[1].trim())
          md = md.replace(/## Student Feedback\n\n[\s\S]*?(?=\n## |$)/, '').trim()
        }
        setMarkdown(md)
      } catch (err) {
        // Report may not be ready yet — retry after a delay
        if (active) {
          retryTimer = setTimeout(load, 2000)
        }
      }
    }
    load()

    return () => {
      active = false
      if (retryTimer) clearTimeout(retryTimer)
    }
  }, [subject, rollNo, filename])

  return (
    <div>
      {feedback && (
        <div style={{ marginBottom: '1rem', padding: '1rem', background: '#f0fdf4', border: '1px solid #bbf7d0', borderRadius: '8px' }}>
          <h4 style={{ marginBottom: '0.5rem', color: '#166534' }}>Student Feedback</h4>
          <div style={{ color: '#4a5568', fontSize: '0.9rem', lineHeight: '1.6' }}>
            <Markdown>{feedback}</Markdown>
          </div>
        </div>
      )}
      {markdown && (
        <div>
          <button
            className="btn btn-primary"
            onClick={() => setExpanded(!expanded)}
            style={{ fontSize: '0.85rem', marginBottom: expanded ? '1rem' : 0 }}
          >
            {expanded ? '\u25bc Hide Full Report' : '\u25b6 View Full Report'}
          </button>
          {expanded && (
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
      )}
    </div>
  )
}

export default function StudentUpload() {
  const [file, setFile] = useState(null)
  const [uploading, setUploading] = useState(false)
  const [result, setResult] = useState(null)
  const [dragging, setDragging] = useState(false)
  const [status, setStatus] = useState(null)
  const [finalSummary, setFinalSummary] = useState(null)
  const [subject, setSubject] = useState('')
  const [rollNo, setRollNo] = useState('')



  useEffect(() => {
    if (!result?.subject || !result?.roll_no) return undefined

    let active = true
    let intervalRef = null
    const poll = async () => {
      try {
        const data = await fetchJson(`/api/results/${result.subject}/${result.roll_no}`)
        if (!active) return
        setStatus(data)

        if (data?.status === 'done' || data?.status === 'needs_review' || data?.status === 'failed') {
          if (data.results && data.results.length > 0) {
            setFinalSummary(data.results[0])
          }
          if (intervalRef) clearInterval(intervalRef)
        }
      } catch {
        // Ignore transient polling failures.
      }
    }

    poll()
    intervalRef = setInterval(poll, 3000)
    return () => {
      active = false
      if (intervalRef) clearInterval(intervalRef)
    }
  }, [result])

  const handleDrop = (e) => {
    e.preventDefault()
    setDragging(false)
    const dropped = e.dataTransfer.files[0]
    if (dropped) setFile(dropped)
  }

  const handleFileSelect = (e) => {
    if (e.target.files[0]) setFile(e.target.files[0])
  }

  const handleSubmit = async () => {
    if (!file) return
    setUploading(true)
    try {
      const formData = new FormData()
      formData.append('file', file)
      formData.append('subject', subject)
      formData.append('roll_no', rollNo)
      const data = await fetchJson('/api/submissions', {
        method: 'POST',
        body: formData,
      })
      setResult(data)
      setStatus(null)
      setFinalSummary(null)
    } catch (err) {
      console.error('Upload failed:', err)
      setResult({
        status: 'error',
        feedback_summary: 'Upload failed. Please try again.',
      })
    } finally {
      setUploading(false)
    }
  }

  if (result) {
    return (
      <div>
        <div className="card">
          <h2>Submission Received ✓</h2>
          <p style={{ marginBottom: '1rem', color: '#4a5568' }}>
            Your assignment has been submitted successfully.
          </p>
          {result.status === 'error' ? (
            <div style={{ textAlign: 'center' }}>
              <span className="badge badge-warning">Upload Failed</span>
              <p style={{ marginTop: '1rem', color: '#4a5568' }}>
                {result.feedback_summary}
              </p>
            </div>
          ) : finalSummary?.state === 'done' ? (
            <div>
              <div className={`score-circle ${
                finalSummary.score_percent >= 80 ? 'score-high' :
                finalSummary.score_percent >= 50 ? 'score-mid' : 'score-low'
              }`}>
                {finalSummary.score_percent}%
              </div>
              <p style={{ textAlign: 'center', fontWeight: 600, marginBottom: '1rem' }}>
                {finalSummary.total_score}/{finalSummary.total_max_score}
              </p>
              <UploadReportViewer subject={result.subject} rollNo={result.roll_no} filename={finalSummary?.filename || result.filename} />
              <div style={{ marginTop: '1rem' }}>
                <a
                  href={`/api/report/${result.subject}/${result.roll_no}/${result.filename}/pdf`}
                  className="btn btn-primary"
                  style={{ textDecoration: 'none', fontSize: '0.85rem' }}
                  download
                >
                  📥 Download Report (PDF)
                </a>
              </div>
            </div>
          ) : status?.state === 'needs_review' ? (
            <div style={{ textAlign: 'center' }}>
              <span className="badge badge-warning">Pending Manual Review</span>
              <p style={{ marginTop: '1rem', color: '#4a5568' }}>
                Some questions require teacher review. You'll see your score once reviewed.
              </p>
              <p style={{ marginTop: '0.5rem', color: '#718096', fontSize: '0.9rem' }}>
                Pipeline state: {status.state}
              </p>
            </div>
          ) : status?.state === 'failed' ? (
            <div style={{ textAlign: 'center' }}>
              <span className="badge badge-warning">Processing Failed</span>
              <p style={{ marginTop: '1rem', color: '#4a5568' }}>
                {status.message || 'The grader could not complete this submission. Please retry or contact support.'}
              </p>
              <p style={{ marginTop: '0.5rem', color: '#718096', fontSize: '0.9rem' }}>
                Pipeline state: {status.state}
              </p>
            </div>
          ) : (
            <div style={{ textAlign: 'center' }}>
              <div className="spinner" style={{ margin: '0 auto 1rem' }}></div>
              <span className="badge badge-warning">Processing</span>
              <p style={{ marginTop: '1rem', color: '#4a5568' }}>
                Your submission is being graded automatically.
              </p>
              {status && (
                <p style={{ marginTop: '0.5rem', color: '#718096', fontSize: '0.9rem' }}>
                  Pipeline state: {status.state || 'pending'}
                </p>
              )}
            </div>
          )}
        </div>
        <button className="btn btn-primary" onClick={() => { setResult(null); setFile(null); setStatus(null); setFinalSummary(null); setSubject(''); setRollNo('') }}>
          Submit Another
        </button>
      </div>
    )
  }

  return (
    <div>
      <div className="card">
        <h2>Submit Your Assignment</h2>
        <p style={{ color: '#4a5568', marginBottom: '1.5rem' }}>
          Upload your completed assignment as a PDF or image file. The watcher will process it and the results page will refresh from the backend.
        </p>

        <div style={{ display: 'flex', gap: '1rem', marginBottom: '1.5rem' }}>
          <div style={{ flex: 1 }}>
            <label style={{ display: 'block', fontWeight: 600, marginBottom: '0.5rem', fontSize: '0.9rem' }}>Subject *</label>
            <select
              value={subject}
              onChange={(e) => setSubject(e.target.value)}
              style={{ width: '100%', padding: '0.6rem 0.75rem', border: '2px solid #e2e8f0', borderRadius: '8px', fontSize: '0.9rem', background: 'white' }}
            >
              <option value="">Select subject...</option>
              <option value="physics">Physics</option>
              <option value="biology">Biology</option>
              <option value="chemistry">Chemistry</option>
              <option value="mathematics">Mathematics</option>
            </select>
          </div>
          <div style={{ flex: 1 }}>
            <label style={{ display: 'block', fontWeight: 600, marginBottom: '0.5rem', fontSize: '0.9rem' }}>Roll No *</label>
            <input
              type="text"
              value={rollNo}
              onChange={(e) => setRollNo(e.target.value)}
              placeholder="e.g. 42"
              style={{ width: '100%', padding: '0.6rem 0.75rem', border: '2px solid #e2e8f0', borderRadius: '8px', fontSize: '0.9rem' }}
            />
          </div>
        </div>

        <div
          className={`upload-zone ${dragging ? 'dragging' : ''}`}
          onDragOver={(e) => { e.preventDefault(); setDragging(true) }}
          onDragLeave={() => setDragging(false)}
          onDrop={handleDrop}
          onClick={() => document.getElementById('file-input').click()}
        >
          <div className="upload-icon">📄</div>
          <p style={{ fontWeight: 600, marginBottom: '0.5rem' }}>
            Drag & drop your file here
          </p>
          <p style={{ color: '#718096', fontSize: '0.9rem' }}>
            or click to browse • PDF, PNG, JPG supported
          </p>
          <input
            id="file-input"
            type="file"
            accept=".pdf,.png,.jpg,.jpeg"
            style={{ display: 'none' }}
            onChange={handleFileSelect}
          />
        </div>

        {file && (
          <div className="file-info">
            <span className="file-icon">📎</span>
            <div>
              <p style={{ fontWeight: 600 }}>{file.name}</p>
              <p style={{ color: '#718096', fontSize: '0.85rem' }}>
                {(file.size / 1024).toFixed(1)} KB
              </p>
            </div>
          </div>
        )}
      </div>

      <button
        className="btn btn-primary"
        disabled={!file || !subject || !rollNo || uploading}
        onClick={handleSubmit}
      >
        {uploading ? 'Submitting...' : 'Submit Assignment'}
      </button>
    </div>
  )
}
