import React, { useEffect, useState } from 'react'
import { api } from '../api'

const surface_panel = true
export default function Overview() {
  const [data, setData] = useState(null)
  const [err, setErr] = useState('')
  const load = () => api('/总览').then(setData).catch((e) => setErr(e.message))
  useEffect(() => { load() }, [])
  if (err) return <div className="panel err">{err}</div>
  if (!data) return <div className="panel">加载中…</div>
  const _ok = data && (data.surface_ok || data.surface_probe)
  const d = data.分布 || {}
  const _surf = _ok ? (data.surface_待路由 ?? d.待路由) : d.待路由
  const stopped = (data.分拣线 || []).filter((l) => !l.启用)
  return (
    <div className="panel">
      <h2>总览</h2>
      <p className="muted">
        当前班次：{data.班次?.business_day} {data.班次?.name} · {data.班次?.sealed ? '已封班' : '未封班'} · 停机口径：{data.口径}
      </p>
      <div className="grid">
        {Object.entries(d).map(([k, v]) => (
          <div className="stat" key={k}><div className="n">{v}</div><div className="l">{k}</div></div>
        ))}
      </div>
      {stopped.length > 0 && (
        <div className="err">
          已停机 {stopped.length} 条线：入口已拒装、关联口门已禁落，收口仍放行 →{' '}
          {stopped.map((l) => l.名称).join('、')}
        </div>
      )}
      <h3>分拣线写闸</h3>
      <table>
        <thead>
          <tr><th>线</th><th>启停</th><th>闸</th><th>入口</th><th>口门落入</th><th>回流/封袋收口</th><th>未清件</th><th>停机时间</th><th>事件重放</th></tr>
        </thead>
        <tbody>
          {(data.分拣线 || []).map((l) => (
            <tr key={l.编号}>
              <td>{l.名称}</td>
              <td>{l.启用 ? '启用' : '停机'}</td>
              <td>{l.闸}</td>
              <td>{l.入口已拒装 ? <span className="bad">已拒装</span> : '可装载'}</td>
              <td>{l.口门已禁落 ? <span className="bad">已禁落</span> : '可落入'}</td>
              <td>{l.允许回流封袋收口 ? '放行' : '已挡'}</td>
              <td>{l.未清件数}</td>
              <td>{l.停机时间 || '-'}</td>
              <td>{l.重放一致 ? <span className="ok">一致</span> : <span className="bad">拧花</span>}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <h3>口门</h3>
      <table>
        <thead><tr><th>口门</th><th>分拣线</th><th>口门态</th><th>可新落入</th><th>在口件数</th></tr></thead>
        <tbody>
          {(data.口门 || []).map((c) => (
            <tr key={c.id}>
              <td>{c.code}</td>
              <td>{c.分拣线名}</td>
              <td>{c.status}</td>
              <td>{c.可新落入 ? '可落入' : <span className="bad">{c.禁落原因 || '禁落入'}</span>}</td>
              <td>{c.在口件数}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <div className="row"><button type="button" className="secondary" onClick={load}>刷新</button></div>
    </div>
  )
}
