import React, { useEffect, useState } from 'react'
import { api } from '../api'

export default function Scans() {
  const [rows, setRows] = useState([])
  const [lines, setLines] = useState([])
  const [chutes, setChutes] = useState([])
  const [routes, setRoutes] = useState([])
  const [lineId, setLineId] = useState('')
  const [barcode, setBarcode] = useState('')
  const [dest, setDest] = useState('110')
  const [msg, setMsg] = useState('')
  const [err, setErr] = useState('')
  const load = async () => {
    setRows(await api('/扫描'))
    const ls = await api('/分拣线'); setLines(ls)
    setChutes(await api('/口门'))
    setRoutes(await api('/路由表'))
    if (!lineId && ls[0]) setLineId(String(ls[0].id))
  }
  useEffect(() => { load().catch((e) => setErr(e.message)) }, [])
  const current = lines.find((l) => String(l.id) === lineId)
  const entryBlocked = current && !current.闸?.允许装载

  // 裁决目标口门是否已被停机线禁落（与后端 require_routing_allowed 同口径）
  const chuteById = Object.fromEntries(chutes.map((c) => [c.id, c]))
  const destChute = {}
  for (const r of routes) destChute[r.dest_code] = chuteById[r.chute_id]
  const routeHint = (r) => {
    const c = destChute[r.dest_code]
    if (!c) return null
    if (!c.可新落入) return c.禁落原因
    return null
  }

  const create = async () => {
    setErr(''); setMsg('')
    try {
      await api('/扫描', { method: 'POST', body: JSON.stringify({ 条码: barcode, 目的地码: dest, 分拣线编号: Number(lineId) }) })
      setMsg('过机装载成功'); setBarcode(''); load()
    } catch (e) { setErr(e.message) }
  }
  const routeOne = async (id) => {
    setErr(''); setMsg('')
    try {
      await api(`/扫描/${id}/路由`, { method: 'POST' })
      setMsg('路由成功'); load()
    } catch (e) { setErr(e.message) }
  }
  return (
    <div className="panel">
      <h2>过机扫描</h2>
      <div className="row">
        <label>分拣线
          <select value={lineId} onChange={(e) => setLineId(e.target.value)}>
            {lines.map((l) => (
              <option key={l.id} value={l.id}>{l.name} · {l.闸?.闸}</option>
            ))}
          </select>
        </label>
        <label>条码<input value={barcode} onChange={(e) => setBarcode(e.target.value)} /></label>
        <label>目的地码<input value={dest} onChange={(e) => setDest(e.target.value)} /></label>
        <button type="button" onClick={create} disabled={entryBlocked}>装载扫描</button>
      </div>
      {entryBlocked
        ? <div className="err">该线已停机：入口已拒装（新件禁止过机），已有件仍可裁决/回流/封袋收口。</div>
        : current && <div className="ok">该线闸=开：入口可装载、口门可落入、收口放行。</div>}
      {msg && <div className="msg">{msg}</div>}
      {err && <div className="err">{err}</div>}
      <table>
        <thead><tr><th>编号</th><th>条码</th><th>目的地</th><th>状态</th><th>线</th><th>口门</th><th>裁决提示</th><th>操作</th></tr></thead>
        <tbody>
          {rows.map((r) => {
            const hint = routeHint(r)
            return (
              <tr key={r.id}>
                <td>{r.id}</td><td>{r.barcode}</td><td>{r.dest_code}</td><td>{r.status}</td>
                <td>{lines.find((l) => l.id === r.line_id)?.name || r.line_id}</td>
                <td>{r.chute_id || '-'}</td>
                <td>{hint ? <span className="bad">{hint}</span> : '-'}</td>
                <td>
                  {(r.status === '待路由' || r.status === '错分回流') &&
                    <button type="button" onClick={() => routeOne(r.id)} disabled={!!hint}>路由</button>}
                </td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}
