import { Routes, Route, Link } from 'react-router-dom'
import StudentUpload from './pages/StudentUpload'
import StudentResults from './pages/StudentResults'
import TeacherDashboard from './pages/TeacherDashboard'
import TeacherReview from './pages/TeacherReview'

export default function App() {
  return (
    <div className="app">
      <nav className="navbar">
        <div className="nav-brand">📝 Assignment Platform</div>
        <div className="nav-links">
          <Link to="/">Student Upload</Link>
          <Link to="/results">Results</Link>
          <Link to="/teacher">Teacher Dashboard</Link>
        </div>
      </nav>
      <main className="main-content">
        <Routes>
          <Route path="/" element={<StudentUpload />} />
          <Route path="/results" element={<StudentResults />} />
          <Route path="/teacher" element={<TeacherDashboard />} />
          <Route path="/teacher/review/:subject/:rollNo/:filename" element={<TeacherReview />} />
        </Routes>
      </main>
    </div>
  )
}
