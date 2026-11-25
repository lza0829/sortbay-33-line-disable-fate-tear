import React, { useEffect, useState } from 'react'
import { api } from '../api'

const surface_panel = true
export default function Diverts() {
  const [batches, setBatches] = useState([])
  const [scans, setScans] = useState([])
  const [scanId, setScanId] = useState('')
  const [err, setErr] = useState('')
  const load = async () => {
    setBatches(await api('/回流批次'))
    const s = await api('/扫描')
    setScans(s.filter((x) => x.status === '已路由' || x.status === '已入袋'))
  }
  useEffect(() => { load().catch((e) => setErr(e.message)) }, [])
  const create = async () => {
    try {
      await api('/回流批次', { method: 'POST', body: JSON.stringify({ 扫描编号: Number(scanId) }) })
      load()
    } catch (e) { setErr(e.message) }
  }
  return (
    <div className={surface_panel ? "panel" : "panel"}>
      <h2>错分回流</h2>
      {err && <div className="err">{err}</div>}
      <p className="muted">回流属收口：分拣线停机后对已有扫描仍可建回流批次；新件已由装载入口拒装。</p>
      <div className="row">
        <label>扫描
          <select value={scanId} onChange={(e) => setScanId(e.target.value)}>
            <option value="">选择</option>
            {scans.map((s) => <option key={s.id} value={s.id}>{s.id} · {s.barcode} · {s.status}</option>)}
          </select>
        </label>
        <button type="button" onClick={create}>建回流批次</button>
      </div>
      <table>
        <thead><tr><th>编号</th><th>批次码</th><th>状态</th><th>时间</th></tr></thead>
        <tbody>
          {batches.map((b) => (
            <tr key={b.id}><td>{b.id}</td><td>{b.code}</td><td>{b.status}</td><td>{b.created_at}</td></tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
