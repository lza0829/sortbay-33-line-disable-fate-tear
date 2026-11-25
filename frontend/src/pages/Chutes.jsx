import React, { useEffect, useState } from 'react'
import { api } from '../api'

export default function Chutes() {
  const [rows, setRows] = useState([])
  const [err, setErr] = useState('')
  const load = () => api('/口门').then(setRows).catch((e) => setErr(e.message))
  useEffect(() => { load() }, [])
  const toggle = async (id, 状态) => {
    try { await api(`/口门/${id}/开闭?状态=${encodeURIComponent(状态)}`, { method: 'POST' }); load() }
    catch (e) { setErr(e.message) }
  }
  return (
    <div className="panel">
      <h2>口门</h2>
      <p className="muted">分拣线停机后，其关联口门一律「线停机·禁落入」，不得显示成仍可裁决；回流与封袋收口不在此列。</p>
      {err && <div className="err">{err}</div>}
      <table>
        <thead>
          <tr><th>口门码</th><th>标题</th><th>分拣线</th><th>线启停</th><th>口门态</th><th>可新落入</th><th>禁落原因</th><th>收口</th><th>软容量</th><th>操作</th></tr>
        </thead>
        <tbody>
          {rows.map((c) => (
            <tr key={c.id}>
              <td>{c.code}</td>
              <td>{c.title}</td>
              <td>{c.分拣线名}</td>
              <td>{c.线启停}</td>
              <td>{c.status}</td>
              <td>{c.可新落入 ? '可落入' : <span className="bad">禁落入</span>}</td>
              <td>{c.禁落原因 || '-'}</td>
              <td>{c.允许收口 ? '放行' : '已挡'}</td>
              <td>{c.soft_cap}</td>
              <td>
                <button type="button" onClick={() => toggle(c.id, '口门开')}>开</button>{' '}
                <button type="button" className="secondary" onClick={() => toggle(c.id, '口门闭')}>闭</button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
