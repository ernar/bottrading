interface Props {
  onClose: () => void
}

export function DuplicateInstanceModal({ onClose }: Props) {
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 backdrop-blur-sm">
      <div className="bg-gray-800 border border-red-500/60 rounded-xl shadow-2xl p-8 max-w-md w-full mx-4">
        <div className="flex items-center gap-3 mb-4">
          <span className="text-red-400 text-3xl">⚠</span>
          <h2 className="text-xl font-bold text-red-400">Instancia duplicada detectada</h2>
        </div>
        <p className="text-gray-300 mb-2">
          Se intentó iniciar una segunda instancia del bot mientras esta ya está activa.
        </p>
        <p className="text-gray-400 text-sm mb-6">
          Solo puede haber una instancia en ejecución a la vez. Cierra la ventana del nuevo proceso
          o detén el bot actual antes de volver a iniciarlo.
        </p>
        <button
          onClick={onClose}
          className="w-full bg-red-600 hover:bg-red-700 text-white font-semibold py-2 px-4 rounded-lg transition-colors"
        >
          Entendido
        </button>
      </div>
    </div>
  )
}
