"""trick_all_top.csv の上位場面を、目視で判定し直すための確認ページにする（評価用の開発ツール）。

各場面について、時刻の前後1秒を0.5秒おきに5コマ取り出し（幅640）、1ページに並べる。
Claude の判定（judge 列）を初期値として選んだ状態にし、利用者が直して「判定結果を作る」を押すと、
貼り付け用のテキストができる。

出力（<day>/eval/top_review/）：
  rNN_k.jpg     場面 NN の k 番目のコマ（k=0..4、時刻 t-1.0〜t+1.0）
  review.html   確認ページ（ブラウザで開く。外部への通信なし）

使い方：
  .venv/Scripts/python.exe -I owner_eval/make_top_review.py dataset/owner/2026-10-07_10
"""
from __future__ import annotations

import argparse
import csv
import html
import subprocess
import sys
from pathlib import Path

OFFSETS = (-1.0, -0.5, 0.0, 0.5, 1.0)


def extract(video: Path, t: float, out_dir: Path, rank: int) -> None:
    start = max(0.0, t + OFFSETS[0])
    pattern = out_dir / f"r{rank:02d}_%d.jpg"
    for extra in (["-hwaccel", "auto"], []):
        cmd = ["ffmpeg", "-v", "error", "-y", *extra, "-ss", f"{start:.2f}", "-i", str(video),
               "-t", "2.1", "-vf", "fps=2,scale=640:-2", "-start_number", "0", "-q:v", "3", str(pattern)]
        if subprocess.run(cmd, capture_output=True).returncode == 0:
            return
    print(f"取り出せません: {video.name} {t}", file=sys.stderr)


PAGE = """<!DOCTYPE html>
<html lang="ja"><head><meta charset="utf-8"><title>トリック判定の確認（上位{n}場面）</title>
<style>
body{{font-family:'Hiragino Sans','Yu Gothic UI',sans-serif;margin:0;padding:16px 20px 120px;color:#212529;background:#fff}}
h1{{font-size:20px}} .m{{color:#64707C;font-size:13px}}
.card{{border:1px solid #DEE2E6;border-radius:10px;padding:10px 14px;margin:14px 0}}
.card.trick{{border-left:5px solid #DC2626}}
.hd{{display:flex;gap:16px;align-items:center;flex-wrap:wrap;font-size:14px}}
.hd b{{font-size:16px}}
.strip{{display:grid;grid-template-columns:repeat(5,1fr);gap:4px;margin-top:8px}}
.strip a img{{width:100%;display:block;border-radius:4px}}
.strip .c img{{outline:3px solid #DC2626;outline-offset:-3px}}
.cap{{font-size:11px;color:#64707C;text-align:center}}
label{{margin-right:10px;cursor:pointer}} input[type=text]{{width:260px}}
.bar{{position:fixed;bottom:0;left:0;right:0;background:#fff;border-top:1px solid #DEE2E6;padding:10px 20px}}
button{{background:#2563EB;color:#fff;border:0;border-radius:8px;padding:8px 20px;font-size:14px;cursor:pointer}}
textarea{{width:100%;height:110px;display:none;margin-top:6px;font-family:monospace}}
</style></head><body>
<h1>トリック判定の確認（「トリックらしさ」上位{n}場面）</h1>
<p class="m">1場面につき、前後1秒を0.5秒おきに5コマ並べています（真ん中の赤枠が点数の高かったコマ）。画像をクリックすると大きく開きます。
選択肢の初期値は Claude の判定です。違うものだけ直し、最後に下の「判定結果を作る」を押して、出たテキストをチャットに貼ってください。
「オーナー」は、オーナーが完成動画に使った区間に入っているか（S1＝トリックとして使用、S2＝走行として使用）。</p>
{cards}
<div class="bar"><button onclick="gen()">判定結果を作る</button> <span id="st" class="m"></span>
<textarea id="out" readonly onclick="this.select()"></textarea></div>
<script>
var KEY="trick-top-review-v1";
function save(){{var d={{}};document.querySelectorAll('input').forEach(function(e){{if(e.type==='radio'&&e.checked)d[e.name]=e.value;if(e.type==='text')d[e.name]=e.value;}});try{{localStorage.setItem(KEY,JSON.stringify(d));}}catch(e){{}}mark();}}
function load(){{try{{var d=JSON.parse(localStorage.getItem(KEY)||'{{}}');Object.keys(d).forEach(function(k){{var r=document.querySelector('input[name="'+k+'"][value="'+d[k]+'"]');if(r)r.checked=true;var t=document.querySelector('input[type=text][name="'+k+'"]');if(t)t.value=d[k];}});}}catch(e){{}}mark();}}
function mark(){{document.querySelectorAll('.card').forEach(function(c){{var r=c.querySelector('input[type=radio]:checked');c.classList.toggle('trick',!!r&&r.value==='トリック');}});}}
function gen(){{var lines=['【トリック判定の確認】'],changed=0;document.querySelectorAll('.card').forEach(function(c){{var k=c.dataset.rank,orig=c.dataset.orig;var r=c.querySelector('input[type=radio]:checked');var v=r?r.value:orig;var note=c.querySelector('input[type=text]').value.trim();if(v!==orig||note){{changed++;lines.push('#'+k+' '+c.dataset.clip+' t'+c.dataset.t+': '+(v!==orig?orig+' → '+v:v+'（変更なし）')+(note?' / '+note:''));}}}});if(!changed)lines.push('変更なし（Claude の判定のとおり）');var o=document.getElementById('out');o.value=lines.join('\\n');o.style.display='block';o.select();try{{navigator.clipboard.writeText(o.value);document.getElementById('st').textContent='コピーしました。チャットに貼ってください。';}}catch(e){{document.getElementById('st').textContent='下のテキストをコピーしてチャットに貼ってください。';}}}}
document.addEventListener('change',save);document.addEventListener('input',save);load();
</script></body></html>"""


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("day_dir", type=Path)
    args = ap.parse_args()

    eval_dir = args.day_dir / "eval"
    out_dir = eval_dir / "top_review"
    out_dir.mkdir(exist_ok=True)
    with (eval_dir / "trick_all_top.csv").open(encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))

    cards = []
    for r in rows:
        rank, t = int(r["rank"]), float(r["t"])
        extract(args.day_dir / "raw" / r["clip"], t, out_dir, rank)
        orig = "トリック" if r["judge"] == "トリック" else ("不明" if not r["judge"] else "トリックでない")
        imgs = []
        start = max(0.0, t + OFFSETS[0])  # extract() と同じ開始位置。k 番目のコマは start + 0.5k 秒
        names = [f"r{rank:02d}_{k}.jpg" for k in range(len(OFFSETS)) if (out_dir / f"r{rank:02d}_{k}.jpg").exists()]
        center = min(range(len(names)), key=lambda k: abs(start + 0.5 * k - t)) if names else -1
        for k, name in enumerate(names):
            cls = ' class="c"' if k == center else ""
            imgs.append(f'<div{cls}><a href="{name}" target="_blank"><img src="{name}" loading="lazy"></a>'
                        f'<div class="cap">{start + 0.5 * k:.1f}秒</div></div>')
        opts = "".join(
            f'<label><input type="radio" name="j{rank}" value="{v}"{" checked" if v == orig else ""}>{v}</label>'
            for v in ("トリック", "トリックでない", "不明"))
        cards.append(
            f'<div class="card" data-rank="{rank}" data-clip="{html.escape(r["clip"])}" data-t="{t:.2f}" '
            f'data-orig="{orig}"><div class="hd"><b>#{rank}</b><span>{html.escape(r["clip"])}　{t:.2f}秒</span>'
            f'<span>点数 {float(r["score"]):.3f}</span><span>オーナー: {r["owner"] or "使っていない"}</span>'
            f'<span class="m">Claude: {html.escape(r["judge"])}</span></div>'
            f'<div class="strip">{"".join(imgs)}</div>'
            f'<div style="margin-top:6px">{opts}<input type="text" name="n{rank}" placeholder="メモ（任意）"></div></div>')
        print(f"#{rank} {r['clip']} {t}", flush=True)

    (out_dir / "review.html").write_text(PAGE.format(n=len(rows), cards="\n".join(cards)), encoding="utf-8")
    print(f"保存: {out_dir / 'review.html'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
