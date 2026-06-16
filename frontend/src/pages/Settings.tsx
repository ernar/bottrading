import { BotSettings } from '../components/BotSettings'

export function SettingsPage() {
  return (
    <div className="p-4 sm:p-8">
      <h1 className="text-2xl font-bold mb-6">Ajustes</h1>
      <BotSettings />
    </div>
  )
}
