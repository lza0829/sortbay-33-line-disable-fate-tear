import React, { useEffect, useState } from 'react'
import { api } from '../api'

export default function Audit() {
  const [rows, setRows] = useState([])
  const [lineEvents, setLineEvents] = useState([])
  const [err, setErr] = useState('')
  const load = async () => {
    setRows(await api('/审计'))
    setLineEvents(await api('/审计/线事件'))
  }
  useEffect(() => { load().catch((e) => setErr(e.message)) }, [])
  return (
    <div className="panel">
      <h2>审计</h2>
      {err && <div className="err">{err}</div>}
      <h3>停机/恢复线事件</h3>
      <table>
        <thead><tr><th>编号</th><th>分拣线</th><th>动作</th><th>闸</th><th>关联口门</th><th>时间</th><th>备注</th></tr></thead>
        <tbody>
          {lineEvents.map((e) => (
            <tr key={`le-${e.id}`}>
              <td>{e.id}</td><td>{e.分拣线名}</td><td>{e.action}</td><td>{e.gate}</td>
              <td>{e.chute_count}</td><td>{e.created_at}</td><td>{e.note}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <h3>写口审计</h3>
      <table>
        <thead><tr><th>编号</th><th>类别</th><th>引用</th><th>动作</th><th>时间</th><th>备注</th></tr></thead>
        <tbody>
          {rows.map((e, i) => (
            <tr key={`a-${e.kind}-${e.id}-${i}`}>
              <td>{e.id}</td><td>{e.kind}</td><td>{e.ref_id ?? '-'}</td><td>{e.action}</td>
              <td>{e.created_at}</td><td>{e.note}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <div className="row"><button type="button" className="secondary" onClick={load}>刷新</button></div>
    </div>
  )
}
