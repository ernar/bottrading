import { Component, ErrorInfo, ReactNode } from 'react'

interface Props {
  children: ReactNode
}

interface State {
  error: Error | null
  info: ErrorInfo | null
}

// Evita la "pantalla en blanco": captura cualquier error de render de los hijos
// y muestra el mensaje + stack en lugar de dejar el DOM vacío.
export class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null, info: null }

  static getDerivedStateFromError(error: Error): Partial<State> {
    return { error }
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    this.setState({ error, info })
    // Deja rastro también en la consola para depurar.
    console.error('Error capturado por ErrorBoundary:', error, info)
  }

  render() {
    const { error, info } = this.state
    if (!error) return this.props.children

    return (
      <div className="min-h-screen bg-gray-900 text-white p-6">
        <div className="max-w-3xl mx-auto bg-gray-800 border border-red-500/60 rounded-lg p-6">
          <h1 className="text-xl font-bold text-red-400 mb-2">Se produjo un error en el dashboard</h1>
          <p className="text-sm text-gray-400 mb-4">
            La interfaz capturó una excepción al renderizar. Detalle:
          </p>
          <pre className="text-xs text-red-300 bg-gray-900 rounded p-3 overflow-auto whitespace-pre-wrap">
            {error.message}
            {info?.componentStack}
          </pre>
          <button
            onClick={() => this.setState({ error: null, info: null })}
            className="mt-4 px-3 py-2 text-sm rounded bg-blue-600 hover:bg-blue-500"
          >
            Reintentar
          </button>
        </div>
      </div>
    )
  }
}
