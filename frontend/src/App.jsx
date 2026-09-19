import React, { useEffect, useRef, useState } from 'react'

const API = (import.meta.env.VITE_API_BASE_URL || 'http://127.0.0.1:8000').replace(/\/$/, '')

const previewCandidates = [
  { seen: '10:04:31', token: 'QEPE', age: '0m 42s', dex: 'Meteora', one: '—', five: '—', hour: '—', status: 'WATCH 1M', contract: '2SYCNJMchpmLC4pWmakBTwdVn4ZqK4au2d4jHh5H8uNW' },
  { seen: '10:04:18', token: 'ZCSH', age: '2m 38s', dex: 'PumpSwap', one: '$38', five: '$454', hour: '$513', status: 'WATCH 1H', contract: 'G74j3MVzocsWEf8vjgdFQgWQ5dhHp2zUgb731szMRQRQ' },
  { seen: '10:04:11', token: 'QUBITS', age: '2m 13s', dex: 'PumpSwap', one: '$23.3K', five: '—', hour: '—', status: 'QUALIFIED', contract: 'D5Gqp1DRGGAnkRoYfeR4roRfmi5Wiat7AxHpWbRy355J' },
  { seen: '10:03:59', token: 'Wojak', age: '6m 07s', dex: 'Meteora', one: '$1.9K', five: '$1.9K', hour: '$1.9K', status: 'WATCH 1H', contract: 'A9xs6B1b2ANRMGRANwEPDqgBa6ktHAQNJ14VdhTDnFfM' },
  { seen: '10:03:47', token: 'BUNNY', age: '3m 16s', dex: 'PumpSwap', one: '$6.9K', five: '—', hour: '—', status: 'QUALIFIED', contract: '6xaNLoAaTmhnjAhed2ueCcLpnGwjt7fw6xssb1mGaov8' },
  { seen: '10:03:34', token: 'S500', age: '4m 51s', dex: 'Meteora', one: '—', five: '—', hour: '—', status: 'RETRYING', contract: '9psZbS4m1hxLcJ41vjRY9dd9CRHkjc1o1saS4d5STNK' },
]

// These illustrative values appear only at ?preview=1; live RVOL always comes from the API.
const previewQualified = [
  { time: '10:04:23', token: 'INTENTS', trigger: '1H', triggerVolume: '$162.2K', age: '11h 12m', price: '$0.00001232', mcap: '$12.3K', vol: '$0', liq: '$0', rvol_1m: 0.72, rvol_5m: 1.87, rvol_1h: 0.72, change: '+33.9%', hourChange: '+43.5%', txns: '0', contract: '9yydsdUByfN3zJdUj1qErDpoZsP2iKvZwvpLQmbUsDZA' },
  { time: '10:04:22', token: 'PWG', trigger: '5M', triggerVolume: '$33.6K', age: '3h 17m', price: '$0.00001193', mcap: '$11.9K', vol: '$0', liq: '$0.69', rvol_1m: 2.43, rvol_5m: 2.43, rvol_1h: null, change: '+30.2%', hourChange: '+61.4%', txns: '0', contract: 'Gp5QrFxhzMTL8z6C19QWF7CYUFkVQuHziijA9tFbDzMJ' },
  { time: '10:04:17', token: 'PaidMini', trigger: '1M', triggerVolume: '$16.5K', age: '14m 15s', price: '$0.000002729', mcap: '$2.7K', vol: '$45', liq: '$2.7K', rvol_1m: 11.8, rvol_5m: null, rvol_1h: null, change: '-3.2%', hourChange: '-93.9%', txns: '3', contract: '6afphVoe6tSjS9FPLyFJsy8kk26bY18QdD8JoCB5pump' },
  { time: '10:04:14', token: 'OGDOGE', trigger: '1M', triggerVolume: '$9.3K', age: '14m 05s', price: '$0.00007157', mcap: '$71.6K', vol: '$68.7K', liq: '$24', rvol_1m: 1, rvol_5m: null, rvol_1h: null, change: '+4.9%', hourChange: '+680.0%', txns: '613', contract: 'HMa3SBtSNA39rDmzAeYfrAvKkrjY7cVXTZX3vem4JU9' },
  { time: '10:04:08', token: 'BUNNY', trigger: '1M', triggerVolume: '$6.9K', age: '3m 16s', price: '$0.00002765', mcap: '$27.7K', vol: '$24.3K', liq: '$44.5K', rvol_1m: null, rvol_5m: null, rvol_1h: null, change: '+41.9%', hourChange: '+41.9%', txns: '706', contract: '6xaNLoAaTmhnjAhed2ueCcLpnGwjt7fw6xssb1mGaov8' },
  { time: '10:04:07', token: 'QUBITS', trigger: '1M', triggerVolume: '$23.3K', age: '2m 13s', price: '$0.00001430', mcap: '$14.3K', vol: '$59.7K', liq: '$22.8K', rvol_1m: null, rvol_5m: null, rvol_1h: null, change: '+55.3%', hourChange: '+55.3%', txns: '592', contract: 'D5Gqp1DRGGAnkRoYfeR4roRfmi5Wiat7AxHpWbRy355J' },
]

const statusLabels = {
  NEW: 'QUEUED', WATCHING_1M: 'WATCH 1M', WATCHING_5M: 'WATCH 5M',
  WATCHING_1H: 'WATCH 1H', QUALIFIED: 'QUALIFIED', ERROR: 'RETRYING',
}

function compact(value) {
  const n = Number(value)
  if (!Number.isFinite(n)) return '—'
  if (Math.abs(n) >= 1e9) return `${(n / 1e9).toFixed(1)}B`
  if (Math.abs(n) >= 1e6) return `${(n / 1e6).toFixed(1)}M`
  if (Math.abs(n) >= 1e3) return `${(n / 1e3).toFixed(1)}K`
  return n.toLocaleString(undefined, { maximumFractionDigits: n < 10 ? 2 : 0 })
}

function money(value) { return value == null ? '—' : `$${compact(value)}` }
function price(value) {
  if (value == null) return '—'
  const number = Number(value)
  if (!Number.isFinite(number)) return '—'
  return `$${number.toLocaleString(undefined, { maximumSignificantDigits: 5 })}`
}
function clock(value) { return value ? new Date(value).toLocaleTimeString([], { hour12: false }) : '—' }
function age(value, now) {
  if (!value) return '—'
  const seconds = Math.max(0, Math.floor((now - value) / 1000))
  if (seconds < 60) return `${seconds}s`
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ${seconds % 60}s`
  return `${Math.floor(seconds / 3600)}h ${Math.floor((seconds % 3600) / 60)}m`
}
function percent(value) {
  if (value == null || !Number.isFinite(Number(value))) return '—'
  return `${Number(value) > 0 ? '+' : ''}${Number(value).toFixed(1)}%`
}
function rvol(value) {
  if (value == null || !Number.isFinite(Number(value))) return '—'
  return `${Number(value).toFixed(Number(value) >= 10 ? 1 : 2)}x`
}

function candidateRow(pair, now) {
  if ('seen' in pair) return pair
  return {
    seen: clock(pair.first_seen_at), token: pair.symbol || '—',
    age: age(pair.pair_created_at || pair.first_seen_at, now), dex: pair.dex_id || '—',
    price: price(pair.price_usd), mcap: money(pair.market_cap), vol: money(pair.volume_h24), liq: money(pair.liquidity_usd),
    one: money(pair.volume_1m), five: money(pair.volume_5m), hour: money(pair.volume_1h),
    status: statusLabels[pair.status] || pair.status, contract: pair.token_address,
  }
}

function qualifiedRow(pair, now) {
  if ('time' in pair) return pair
  return {
    time: clock(pair.qualified_at), token: pair.symbol || '—',
    age: age(pair.pair_created_at || pair.first_seen_at, now),
    trigger: pair.qualification_trigger?.toUpperCase() || '—', triggerVolume: money(pair.qualification_volume),
    price: price(pair.price_usd), chart: pair.chart || [], id: pair.id,
    mcap: money(pair.market_cap), vol: money(pair.volume_h24),
    liq: money(pair.liquidity_usd), rvol_1m: pair.rvol_1m, rvol_5m: pair.rvol_5m,
    rvol_1h: pair.rvol_1h, change: percent(pair.price_change_5m),
    hourChange: percent(pair.price_change_1h), txns: pair.txns_m5 == null ? '—' : compact(pair.txns_m5),
    contract: pair.token_address,
  }
}

async function copyContract(value) {
  if (!value) throw new Error('No contract address')
  try {
    await navigator.clipboard?.writeText(value)
    return
  } catch {}

  const fallback = document.createElement('textarea')
  fallback.value = value
  fallback.setAttribute('readonly', '')
  fallback.style.position = 'fixed'
  fallback.style.opacity = '0'
  document.body.appendChild(fallback)
  fallback.select()
  const copied = document.execCommand('copy')
  fallback.remove()
  if (!copied) throw new Error('Clipboard unavailable')
}

function CopyButton({ value }) {
  const [copied, setCopied] = useState(false)
  if (!value) return <span>—</span>
  async function copy() {
    try {
      await copyContract(value)
      setCopied(true)
      window.setTimeout(() => setCopied(false), 1200)
    } catch { setCopied(false) }
  }
  return <button className="contract" onClick={copy} title={value} aria-label={`Copy contract ${value}`}>{copied ? 'COPIED' : `${value.slice(0, 4)}…${value.slice(-4)}`}</button>
}

function CopyTokenButton({ token, contract }) {
  const [copied, setCopied] = useState(false)
  if (!contract) return <span className="token">{token}</span>
  async function copy() {
    try {
      await copyContract(contract)
      setCopied(true)
      window.setTimeout(() => setCopied(false), 1200)
    } catch { setCopied(false) }
  }
  return <button className="token token-copy" onClick={copy} title="Copy contract address" aria-label={`Copy contract address for ${token}`}>{copied ? 'COPIED' : token}</button>
}

function CandidateTable({ pairs, now, loading }) {
  return <section className="table-panel candidate-panel" aria-labelledby="candidate-heading">
    <div className="table-title"><h2 id="candidate-heading">DISCOVERY</h2><span>{pairs.length} PAIRS</span></div>
    <div className="table-scroll"><table>
      <thead><tr><th>TOKEN</th><th>AGE</th><th>PRICE</th><th>MCAP</th><th>VOL</th><th>LIQ.</th><th>1M #1</th><th>5M #1</th><th>1H #1</th></tr></thead>
      <tbody>{pairs.map(pair => {
        const row = candidateRow(pair, now)
        return <tr key={pair.id || row.contract}>
          <td><CopyTokenButton token={row.token} contract={row.contract} /></td><td>{row.age}</td><td>{row.price || '—'}</td><td>{row.mcap || '—'}</td><td>{row.vol || '—'}</td><td>{row.liq || '—'}</td><td>{row.one}</td><td>{row.five}</td><td>{row.hour}</td>
        </tr>
      })}</tbody>
    </table>{!pairs.length && <div className="table-empty">{loading ? 'Connecting to scanner…' : 'Waiting for newly discovered pairs.'}</div>}</div>
  </section>
}

function RvolCell({ value }) {
  return <td className={value == null ? 'rvol-empty' : 'rvol-cell'}>{rvol(value)}</td>
}

function MicroCandles({ bars }) {
  if (!bars?.length) return <span className="rvol-empty">—</span>
  const candles = bars.filter(bar => ['open', 'high', 'low', 'close'].every(key => Number.isFinite(Number(bar[key]))))
  if (!candles.length) return <span>—</span>
  const low = Math.min(...candles.map(bar => Number(bar.low)))
  const high = Math.max(...candles.map(bar => Number(bar.high)))
  const scale = value => 32 - ((Number(value) - low) / (high - low || 1)) * 28
  const step = 110 / candles.length
  return <svg className="micro-chart" viewBox="0 0 110 34" role="img" aria-label="Last 12 one-minute candlesticks">
    {candles.map((bar, index) => {
      const rising = Number(bar.close) >= Number(bar.open)
      const x = index * step + step / 2
      const top = Math.min(scale(bar.open), scale(bar.close))
      const body = Math.max(1, Math.abs(scale(bar.open) - scale(bar.close)))
      return <g key={bar.time ?? index} className={rising ? 'candle-up' : 'candle-down'}>
        <line x1={x} y1={scale(bar.high)} x2={x} y2={scale(bar.low)} />
        <rect x={x - Math.max(2, step * .28)} y={top} width={Math.max(4, step * .56)} height={body} />
      </g>
    })}
  </svg>
}

function QualifiedTable({ pairs, now, loading, onDelete, deleting }) {
  return <section className="table-panel qualified-panel" aria-labelledby="qualified-heading">
    <div className="table-title"><h2 id="qualified-heading">QUALIFIED SCANNER</h2><span>{pairs.length} PAIRS</span></div>
    <div className="table-scroll"><table>
      <thead><tr><th>TOKEN</th><th>TRIGGER</th><th>AGE</th><th>PRICE</th><th>MCAP</th><th title="Total 24-hour volume">VOL</th><th>LIQ.</th><th title="Current 1m candle divided by prior 9 completed 1m candles">1M RVOL</th><th title="Current 5m candle divided by prior 9 completed 5m candles">5M RVOL</th><th title="Current 1h candle divided by prior 9 completed 1h candles">1H RVOL</th><th>5M %</th><th>1H %</th><th>TXNS</th><th>CHART</th><th aria-label="Delete">×</th></tr></thead>
      <tbody>{pairs.map(pair => {
        const row = qualifiedRow(pair, now)
        return <tr key={pair.id || row.contract}>
          <td><CopyTokenButton token={row.token} contract={row.contract} /></td><td><span className="trigger" title={`First ${row.trigger} candle: ${row.triggerVolume || '—'}`}>{row.trigger || '—'} <small>{row.triggerVolume || '—'}</small></span></td><td>{row.age}</td><td>{row.price || '—'}</td><td>{row.mcap}</td><td>{row.vol}</td><td>{row.liq}</td>
          <RvolCell value={row.rvol_1m} /><RvolCell value={row.rvol_5m} /><RvolCell value={row.rvol_1h} />
          <td className={row.change.startsWith('+') ? 'up' : row.change.startsWith('-') ? 'down' : ''}>{row.change}</td>
          <td className={row.hourChange.startsWith('+') ? 'up' : row.hourChange.startsWith('-') ? 'down' : ''}>{row.hourChange}</td>
          <td>{row.txns}</td><td><MicroCandles bars={row.chart} /></td><td><button className="delete-pair" disabled={!row.id || deleting === row.id} onClick={() => onDelete(row.id)} aria-label={`Delete ${row.token} from qualified pairs`} title="Remove for this session">×</button></td>
        </tr>
      })}</tbody>
    </table>{!pairs.length && <div className="table-empty">{loading ? 'Loading qualified pairs…' : 'No pairs have crossed a first-candle threshold yet.'}</div>}</div>
  </section>
}

export default function App() {
  const preview = new URLSearchParams(window.location.search).get('preview') === '1'
  const [showDiscovery, setShowDiscovery] = useState(true)
  const [candidatePairs, setCandidatePairs] = useState(preview ? previewCandidates.filter(pair => pair.status !== 'QUALIFIED') : [])
  const [qualifiedPairs, setQualifiedPairs] = useState(preview ? previewQualified : [])
  const [health, setHealth] = useState(null)
  const [loading, setLoading] = useState(!preview)
  const [offline, setOffline] = useState(false)
  const [now, setNow] = useState(Date.now())
  const [deleting, setDeleting] = useState(null)
  const [soundEnabled, setSoundEnabled] = useState(() => {
    try { return JSON.parse(localStorage.getItem('scannerSoundEnabled') || 'false') } catch { return false }
  })
  const alertAudio = useRef(null)
  const audioUnlocked = useRef(false)
  const audioUnlocking = useRef(false)
  const priorQualified = useRef(null)
  const soundRef = useRef(soundEnabled)
  soundRef.current = soundEnabled

  useEffect(() => {
    function unlockSound() {
      if (soundRef.current) unlockAudio()
    }
    window.addEventListener('pointerdown', unlockSound)
    return () => window.removeEventListener('pointerdown', unlockSound)
  }, [])

  function unlockAudio() {
    if (audioUnlocked.current || audioUnlocking.current) return
    alertAudio.current ||= new Audio(`${import.meta.env.BASE_URL}sounds/qualified.wav`)
    const audio = alertAudio.current
    audio.preload = 'auto'
    audio.muted = true
    audioUnlocking.current = true
    audio.play().then(() => {
      audio.pause()
      audio.currentTime = 0
      audio.muted = false
      audioUnlocked.current = true
      audioUnlocking.current = false
    }).catch(() => {
      audio.muted = false
      audioUnlocking.current = false
    })
  }

  function playAlert() {
    if (!alertAudio.current) return
    alertAudio.current.currentTime = 0
    alertAudio.current.play().catch(() => {})
  }

  function toggleSound() {
    if (!soundEnabled) unlockAudio()
    setSoundEnabled(value => {
      localStorage.setItem('scannerSoundEnabled', JSON.stringify(!value))
      return !value
    })
  }

  async function deletePair(id) {
    if (!id || deleting) return
    setDeleting(id)
    try {
      const response = await fetch(`${API}/api/qualified/${id}`, { method: 'DELETE' })
      if (!response.ok) throw new Error('Delete failed')
      setQualifiedPairs(rows => rows.filter(pair => pair.id !== id))
      priorQualified.current?.delete(id)
    } catch { setOffline(true) }
    finally { setDeleting(null) }
  }

  useEffect(() => {
    if (preview) return undefined
    let active = true
    let running = false
    let pending = false
    async function refresh() {
      if (running) { pending = true; return }
      running = true
      try {
        const responses = await Promise.all([
          fetch(`${API}/api/candidates`, { cache: 'no-store' }),
          fetch(`${API}/api/qualified`, { cache: 'no-store' }),
          fetch(`${API}/health`, { cache: 'no-store' }),
        ])
        if (responses.some(response => !response.ok)) throw new Error('Scanner API unavailable')
        const [candidates, qualified, status] = await Promise.all(responses.map(response => response.json()))
        if (active) {
          const ids = new Set(qualified.pairs.map(pair => pair.id))
          if (priorQualified.current && soundRef.current && qualified.pairs.some(pair => !priorQualified.current.has(pair.id))) playAlert()
          priorQualified.current = ids
          setCandidatePairs(candidates.pairs)
          setQualifiedPairs(qualified.pairs)
          setHealth(status)
          setOffline(false)
        }
      } catch {
        if (active) setOffline(true)
      } finally {
        running = false
        if (active) setLoading(false)
        if (pending && active) { pending = false; refresh() }
      }
    }
    refresh()
    const fallback = window.setInterval(refresh, 2000)
    const clockInterval = window.setInterval(() => setNow(Date.now()), 1000)
    return () => {
      active = false
      window.clearInterval(fallback)
      window.clearInterval(clockInterval)
    }
  }, [preview])

  const activeCount = preview ? 27 : health?.counts?.active_candidates ?? '—'
  const qualifiedCount = preview ? 16 : health?.counts?.QUALIFIED ?? 0
  const lastScan = preview ? '1s ago' : health?.last_successful_scan ? `${Math.floor(Math.max(0, now - health.last_successful_scan) / 1000)}s ago` : '—'
  const statusText = preview ? 'PREVIEW DATA' : offline ? 'SCANNER OFFLINE' : health?.discovery_status === 'LIVE' ? 'DISCOVERY LIVE' : 'DISCOVERY STALE'

  return <div className="app-shell">
    <header className="topbar">
      <div className="brand"><span className="brand-mark" aria-hidden="true"><i /><i /><i /></span><span>DEXSCREENER <em>SCANNER</em></span></div>
      <div className="live-stats" aria-label={preview ? 'Preview scanner summary' : 'Live scanner summary'}>
        <div><span>ACTIVE CANDIDATES</span><strong>{activeCount}</strong></div>
        <div><span>QUALIFIED PAIRS</span><strong>{qualifiedCount}</strong></div>
        <div><span>LAST SCAN</span><strong className="last-scan">{lastScan}</strong></div>
      </div>
      <div className="header-actions"><button className="discovery-toggle" onClick={toggleSound} aria-pressed={soundEnabled}>SOUND {soundEnabled ? 'ON' : 'OFF'}</button><button className="discovery-toggle" onClick={() => setShowDiscovery(value => !value)} aria-pressed={showDiscovery}>{showDiscovery ? 'HIDE DISCOVERY' : 'SHOW DISCOVERY'}</button><span className={`static-state ${offline ? 'offline' : ''}`} title={offline ? 'Check the Render backend URL.' : health?.last_error || ''}><i />{statusText}</span></div>
    </header>
    <main className={`tables-layout ${showDiscovery ? '' : 'discovery-hidden'}`}>
      {showDiscovery && <CandidateTable pairs={candidatePairs} now={now} loading={loading} />}
      <QualifiedTable pairs={qualifiedPairs} now={now} loading={loading} onDelete={deletePair} deleting={deleting} />
    </main>
  </div>
}
