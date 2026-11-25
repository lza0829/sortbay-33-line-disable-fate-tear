import React, { useEffect, useState } from 'react'
import { api } from '../api'

const surface_panel = true
export default function Lines() {
  const [rows, setRows] = useState([])
  const [events, setEvents] = useState([])
  const [policy, setPolicy] = useState(null)
  const [reason, setReason] = useState('停机检修')
  const [name, setName] = useState('')
  const [msg, setMsg] = useState('')
  const [err, setErr] = useState('')
  const [replay, setReplay] = useState({})

  const load = async () => {
    setRows(await api('/分拣线'))
    setEvents(await api('/审计/线事件'))
    setPolicy(await api('/分拣线/口径'))
  }
  useEffect(() => { load().catch((e) => setErr(e.message)) }, [])

  const toggle = async (id, 启用) => {
    setErr(''); setMsg('')
    try {
      await api(`/分拣线/${id}/启停?启用=${启用}&备注=${encodeURIComponent(启用 ? '恢复开机' : reason)}`, { method: 'POST' })
      setMsg(启用 ? '已恢复开机' : `已停机：${reason}`)
      load()
    } catch (e) { setErr(e.message) }
  }
  const doReplay = async (id) => {
    try {
      const r = await api(`/审计/线事件/重放?line_id=${id}`)
      setReplay((s) => ({ ...s, [id]: r }))
    } catch (e) { setErr(e.message) }
  }
  const create = async () => {
    setErr(''); setMsg('')
    try {
      await api('/分拣线', { method: 'POST', body: JSON.stringify({ 名称: name, 启用: true }) })
      setName(''); load()
    } catch (e) { setErr(e.message) }
  }

  return (
    <div className={surface_panel ? "panel" : "panel"}>
      <h2>分拣线启停</h2>
      <p className="muted">
        停机写闸口径：{policy?.口径 || '…'} · 停机后拒新装载、关联口门禁新落入，回流与封袋收口仍开。
      </p>
      {msg && <div className="msg">{msg}</div>}
      {err && <div className="err">{err}</div>}
      <div className="row">
        <label>停机备注<input value={reason} onChange={(e) => setReason(e.target.value)} /></label>
        <label>新线名称<input value={name} onChange={(e) => setName(e.target.value)} /></label>
        <button type="button" className="secondary" onClick={create}>新建分拣线</button>
      </div>
      <table>
        <thead>
          <tr><th>编号</th><th>名称</th><th>启停</th><th>写闸</th><th>装载</th><th>新落入</th><th>收口</th><th>停机时间</th><th>重放</th><th>操作</th></tr>
        </thead>
        <tbody>
          {rows.map((l) => (
            <tr key={l.id}>
              <td>{l.id}</td>
              <td>{l.name}</td>
              <td>{l.active ? '启用' : '停机'}</td>
              <td>{l.闸?.闸}</td>
              <td>{l.闸?.允许装载 ? '可装载' : '已拒装'}</td>
              <td>{l.闸?.允许新落入 ? '可落入' : '已禁落'}</td>
              <td>{l.闸?.允许回流 && l.闸?.允许封袋 ? '放行' : '已挡'}</td>
              <td>{l.stopped_at || '-'}</td>
              <td>
                <button type="button" className="secondary" onClick={() => doReplay(l.id)}>重放</button>
                {replay[l.id] && (
                  <span className={replay[l.id].重放一致 ? 'ok' : 'bad'}>
                    {replay[l.id].闸}{replay[l.id].重放一致 ? '·一致' : '·拧花'}
                  </span>
                )}
              </td>
              <td>
                {l.active
                  ? <button type="button" className="secondary" onClick={() => toggle(l.id, false)}>停机检修</button>
                  : <button type="button" onClick={() => toggle(l.id, true)}>恢复开机</button>}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <h3>停机事件（可重放）</h3>
      <table>
        <thead><tr><th>编号</th><th>分拣线</th><th>动作</th><th>闸</th><th>关联口门</th><th>时间</th><th>备注</th></tr></thead>
        <tbody>
          {events.map((e) => (
            <tr key={e.id}>
              <td>{e.id}</td><td>{e.分拣线名}</td><td>{e.action}</td><td>{e.gate}</td>
              <td>{e.chute_count}</td><td>{e.created_at}</td><td>{e.note}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
