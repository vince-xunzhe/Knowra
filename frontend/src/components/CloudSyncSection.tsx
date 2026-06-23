/**
 * 云同步 — Settings sub-panel.
 *
 * Responsibilities:
 *   1. Let the user set Supabase URL / anon key / cloud-backend URL
 *      (persisted to localStorage via api/cloud.ts).
 *   2. Email/password sign-in + sign-up against Supabase Auth.
 *   3. Show the current cloud account + sign-out.
 *   4. (Later W5.4) trigger a manual sync run.
 *
 * Visual style mirrors the existing SettingsPage panels — same
 * dark-card + uppercase tiny labels — so it doesn't look bolted on.
 */
import { useState, type FormEvent } from 'react'
import {
  CloudCog, LogIn, LogOut, UserCircle2, KeyRound, Globe, Mail, RefreshCw,
  ChevronDown, ChevronRight,
} from 'lucide-react'

import { useCloudAuth } from '../hooks/useCloudAuth'
import { getLastSyncAt } from '../api/cloud'

type Mode = 'signin' | 'signup'

export default function CloudSyncSection() {
  const auth = useCloudAuth()
  const [mode, setMode] = useState<Mode>('signin')
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [info, setInfo] = useState<string | null>(null)
  const [showAdvanced, setShowAdvanced] = useState(false)

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault()
    setInfo(null)
    try {
      if (mode === 'signin') {
        await auth.signIn(email.trim(), password)
        setPassword('')
      } else {
        const session = await auth.signUp(email.trim(), password)
        setPassword('')
        if (!session) {
          setInfo('注册成功，请到邮箱查收确认邮件后再登录。')
          setMode('signin')
        }
      }
    } catch {
      // error already surfaced via auth.error
    }
  }

  const handleSignOut = async () => {
    setInfo(null)
    await auth.signOut()
  }

  const lastSyncAt = getLastSyncAt()

  return (
    <div className="space-y-5">
      {/* Configuration — defaults are baked in; this is an optional override */}
      <div className="rounded-xl border border-slate-800 bg-slate-950/40 p-4 space-y-4">
        <div className="flex items-start gap-2">
          <CloudCog size={16} className="text-indigo-300 mt-0.5 shrink-0" />
          <div className="min-w-0">
            <p className="text-base font-semibold text-slate-100">云端连接</p>
            <p className="text-xs text-slate-500 mt-1 leading-relaxed">
              默认已连接到 Knowra 云端，直接登录即可。桌面端把抽取结果同步到云后端，移动端只读消费；PDF 仍只留本机，OpenAI key 也只在本机使用、不上传。
            </p>
          </div>
        </div>

        <button
          type="button"
          onClick={() => setShowAdvanced(v => !v)}
          className="inline-flex items-center gap-1 text-xs text-slate-400 hover:text-slate-200 transition-colors"
        >
          {showAdvanced ? <ChevronDown size={13} /> : <ChevronRight size={13} />}
          高级：自定义云端连接（自建后端时才需要）
        </button>

        {showAdvanced && (
          <div className="space-y-4 pt-1">
            <FieldRow
              icon={<Globe size={14} />}
              label="Supabase URL"
              hint="留空即用内置默认值"
              value={auth.config.supabaseUrl}
              placeholder="https://xxxxx.supabase.co"
              onChange={v => auth.updateConfig({ supabaseUrl: v })}
            />
            <FieldRow
              icon={<KeyRound size={14} />}
              label="Supabase anon key"
              hint="Settings → API → Project API keys → anon public（不是 service_role）"
              value={auth.config.supabaseAnonKey}
              placeholder="eyJ..."
              type="password"
              onChange={v => auth.updateConfig({ supabaseAnonKey: v })}
            />
            <FieldRow
              icon={<CloudCog size={14} />}
              label="云后端 URL"
              hint="部署在 Fly.io 等的 FastAPI cloud；不含末尾斜杠。"
              value={auth.config.baseUrl}
              placeholder="https://knowra-cloud.fly.dev"
              onChange={v => auth.updateConfig({ baseUrl: v })}
            />
            {!auth.configured && (
              <p className="text-xs text-amber-300/90">三项都填好后才能登录与同步。</p>
            )}
          </div>
        )}
      </div>

      {/* Auth */}
      <div className="rounded-xl border border-slate-800 bg-slate-950/40 p-4">
        {auth.user ? (
          <div className="space-y-3">
            <div className="flex items-center gap-3">
              <div className="w-10 h-10 rounded-full bg-indigo-500/15 border border-indigo-500/30 flex items-center justify-center shrink-0">
                <UserCircle2 size={20} className="text-indigo-200" />
              </div>
              <div className="min-w-0 flex-1">
                <p className="text-sm text-slate-100 font-medium truncate">
                  {auth.user.display_name || auth.user.email || auth.user.id}
                </p>
                <p className="text-[11px] text-slate-500 truncate font-mono">{auth.user.id}</p>
              </div>
              <button
                onClick={handleSignOut}
                className="inline-flex items-center gap-1.5 text-xs px-3 py-1.5 rounded-lg bg-slate-800/70 hover:bg-slate-700 text-slate-200 border border-slate-700/50"
              >
                <LogOut size={12} />
                登出
              </button>
            </div>
            <div className="flex items-center gap-4 text-[11px] text-slate-500 pt-1 border-t border-slate-800/60">
              <span className="inline-flex items-center gap-1.5">
                <RefreshCw size={11} />
                上次同步：{lastSyncAt ? new Date(lastSyncAt).toLocaleString() : '从未'}
              </span>
            </div>
          </div>
        ) : (
          <form onSubmit={handleSubmit} className="space-y-4">
            <div className="flex items-center gap-2 mb-1">
              <div className="inline-flex rounded-lg bg-slate-900/60 border border-slate-700/60 p-0.5 text-xs">
                {(['signin', 'signup'] as const).map(m => (
                  <button
                    key={m}
                    type="button"
                    onClick={() => { setMode(m); auth.clearError(); setInfo(null) }}
                    className={`px-3 py-1 rounded-md transition-colors ${
                      mode === m
                        ? 'bg-indigo-500/20 text-indigo-100'
                        : 'text-slate-400 hover:text-slate-200'
                    }`}
                  >
                    {m === 'signin' ? '登录' : '注册'}
                  </button>
                ))}
              </div>
            </div>

            <FieldRow
              icon={<Mail size={14} />}
              label="邮箱"
              value={email}
              placeholder="you@example.com"
              type="email"
              onChange={setEmail}
            />
            <FieldRow
              icon={<KeyRound size={14} />}
              label="密码"
              value={password}
              placeholder="至少 6 位"
              type="password"
              onChange={setPassword}
            />

            {auth.error && <p className="text-xs text-red-300">{auth.error}</p>}
            {info && <p className="text-xs text-emerald-300">{info}</p>}

            <button
              type="submit"
              disabled={!auth.configured || auth.signingIn || auth.signingUp || !email || !password}
              className="w-full inline-flex items-center justify-center gap-2 bg-indigo-500 hover:bg-indigo-400 disabled:opacity-50 disabled:cursor-not-allowed text-white text-sm font-medium px-4 py-2 rounded-xl transition-colors"
            >
              <LogIn size={14} />
              {mode === 'signin'
                ? (auth.signingIn ? '登录中…' : '登录')
                : (auth.signingUp ? '注册中…' : '注册')}
            </button>
          </form>
        )}
      </div>
    </div>
  )
}

function FieldRow({
  icon, label, hint, value, placeholder, type = 'text', onChange,
}: {
  icon?: React.ReactNode
  label: string
  hint?: string
  value: string
  placeholder?: string
  type?: 'text' | 'password' | 'email'
  onChange: (v: string) => void
}) {
  return (
    <div>
      <label className="mb-2 flex items-center gap-2 text-[11px] font-semibold uppercase tracking-[0.14em] text-slate-500">
        {icon && <span className="text-slate-500">{icon}</span>}
        {label}
      </label>
      <input
        type={type}
        value={value}
        placeholder={placeholder}
        onChange={e => onChange(e.target.value)}
        autoComplete={type === 'password' ? 'current-password' : 'off'}
        spellCheck={false}
        className="w-full bg-slate-900/60 border border-slate-700/60 rounded-lg text-sm text-slate-200 px-3 py-2 focus:outline-none focus:border-indigo-500/60 focus:bg-slate-900 transition-colors placeholder:text-slate-500 font-mono"
      />
      {hint && <p className="text-xs text-slate-500 mt-1.5 leading-relaxed">{hint}</p>}
    </div>
  )
}
