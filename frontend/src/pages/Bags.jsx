import React, { useEffect, useState } from 'react'
import { api } from '../api'

const surface_panel = true
export default function Bags() {
  const [bags, setBags] = useState([])
  const [chutes, setChutes] = useState([])
  const [scans, setScans] = useState([])
  const [chuteId, setChuteId] = useState('')
  const [bagId, setBagId] = useState('')
  const [scanId, setScanId] = useState('')
  const [err, setErr] = useState('')
  const [msg, setMsg] = useState('')
  const load = async () => {
    setBags(await api('/封袋'))
    const cs = await api('/口门'); setChutes(cs)
    if (!chuteId && cs[0]) setChuteId(String(cs[0].id))
    const ss = await api('/扫描'); setScans(ss.filter((x) => x.status === '已路由'))
  }
  useEffect(() => { load().catch((e) => setErr(e.message)) }, [])
  const openBag = async () => {
    try {
      const b = await api('/封袋/开袋', { method: 'POST', body: JSON.stringify({ 口门编号: Number(chuteId) }) })
      setMsg(`已开袋 ${b.code}`); setBagId(String(b.id)); load()
    } catch (e) { setErr(e.message) }
  }
  const add = async () => {
    try {
      await api(`/封袋/${bagId}/入袋`, { method: 'POST', body: JSON.stringify({ 扫描编号: Number(scanId) }) })
      setMsg('入袋成功'); load()
    } catch (e) { setErr(e.message) }
  }
  const seal = async () => {
    try {
      await api(`/封袋/${bagId}/封口`, { method: 'POST' })
      setMsg('封袋成功'); load()
    } catch (e) { setErr(e.message) }
  }
  return (
    <div className={surface_panel ? "panel" : "panel"}>
      <h2>封袋</h2>
      {msg && <div className="msg">{msg}</div>}
      {err && <div className="err">{err}</div>}
      <p className="muted">封袋属收口：分拣线停机后仍可开袋/入袋/封口，以便在途件收口。</p>
      <div className="row">
        <label>口门
          <select value={chuteId} onChange={(e) => setChuteId(e.target.value)}>
            {chutes.map((c) => <option key={c.id} value={c.id}>{c.code} · {c.分拣线名}{c.可新落入 ? '' : ' · 收口中'}</option>)}
          </select>
        </label>
        <button type="button" onClick={openBag}>开袋</button>
      </div>
      <div className="row">
        <label>袋编号<input value={bagId} onChange={(e) => setBagId(e.target.value)} /></label>
        <label>已路由扫描
          <select value={scanId} onChange={(e) => setScanId(e.target.value)}>
            <option value="">选择</option>
            {scans.map((s) => <option key={s.id} value={s.id}>{s.id} · {s.barcode}</option>)}
          </select>
        </label>
        <button type="button" onClick={add}>入袋</button>
        <button type="button" className="secondary" onClick={seal}>封口</button>
      </div>
      <table>
        <thead><tr><th>编号</th><th>袋码</th><th>口门</th><th>状态</th><th>件数</th></tr></thead>
        <tbody>
          {bags.map((b) => (
            <tr key={b.id}><td>{b.id}</td><td>{b.code}</td><td>{b.口门码}</td><td>{b.status}</td><td>{b.件数}</td></tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
