import { NavLink } from 'react-router-dom'
import './NavBar.css'

// Simple top navigation between the two top-level pages. NavLink (rather
// than plain Link) automatically adds an "active" class to whichever link
// matches the current URL -- react-router does the matching, we just style
// .active in CSS.
export function NavBar() {
  return (
    <nav className="navbar">
      <NavLink to="/" end>
        Fleet Dashboard
      </NavLink>
      <NavLink to="/batteries">Batteries</NavLink>
      <NavLink to="/alerts">Alerts</NavLink>
    </nav>
  )
}
