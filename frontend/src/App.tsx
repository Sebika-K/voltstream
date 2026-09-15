import { useState } from 'react'
import './App.css'

// Roadmap 2.5: this is just the frontend foundation -- proving the toolchain
// (React + TypeScript + Vite) works end to end. The counter below has nothing
// to do with VoltStream; it exists only so we can see React state updates
// working in the browser before we wire up anything real. It gets replaced
// once we connect to the backend API in the next step.
function App() {
  const [count, setCount] = useState(0)

  return (
    <>
      <h1>VoltStream</h1>
      <p>Frontend foundation is running (React + TypeScript + Vite).</p>
      <button onClick={() => setCount((c) => c + 1)}>
        count is {count}
      </button>
    </>
  )
}

export default App
