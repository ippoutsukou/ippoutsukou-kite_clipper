"""元素材すべてを 0.5秒ごとのコマで並べ、「空中にいるコマ」に印を付けるページを作る（評価用の開発ツール）。

トリック検出を「印を付けて学習する」方式で作るための、教師データ作りの入口。
言葉で指定する方式（trick_zeroshot.py / trick_topk.py）では上位40場面の2割しかトリックでなかったため、
元素材のどこが空中かを人が全部の時刻で決め、それを正解にして学習・評価する。

印の意味：
  空中  ライダーが水面を離れているコマ（踏み切り〜着水）。小さなジャンプも含む。
  迷う  遠すぎる・水しぶきで見えないなど、判断がつかないコマ（学習にも評価にも使わない）。
トリック区間（踏み切りの1秒前〜着水の1秒後）は、印から機械的に作るので、ここでは付けない。

出力（<day>/eval/label_page/）：
  <動画名>_NNNN.jpg  動画のコマ（NNNN 番目＝ NNNN×0.5 秒、幅320）
  label.html         印付けページ（ブラウザで開く。外部への通信なし。入力はブラウザ内に自動保存）

使い方：
  .venv/Scripts/python.exe -I owner_eval/make_trick_label_page.py dataset/owner/2026-10-07_10
"""
from __future__ import annotations

import argparse
import csv
import html
import json
import subprocess
import sys
from pathlib import Path

FPS = 2  # 0.5秒ごと
WIDTH = 320


def extract(video: Path, out_dir: Path) -> list[str]:
    stem = video.stem.replace("~", "_")
    done = sorted(out_dir.glob(f"{stem}_*.jpg"))
    if done:
        return [p.name for p in done]
    pattern = out_dir / f"{stem}_%04d.jpg"
    for extra in (["-hwaccel", "auto"], []):
        cmd = ["ffmpeg", "-v", "error", "-y", *extra, "-i", str(video), "-vf", f"fps={FPS},scale={WIDTH}:-2",
               "-start_number", "0", "-q:v", "4", str(pattern)]
        if subprocess.run(cmd, capture_output=True).returncode == 0:
            break
    else:
        print(f"取り出せません: {video.name}", file=sys.stderr)
    return [p.name for p in sorted(out_dir.glob(f"{stem}_*.jpg"))]


def load_owner(day_dir: Path) -> dict[str, list[list[float]]]:
    """オーナーが①トリックとして使った区間（見つける手がかりとして薄く表示するだけ。正解ではない）。"""
    out: dict[str, list[list[float]]] = {}
    path = day_dir / "match" / "segments_reviewed.csv"
    if path.exists():
        with path.open(encoding="utf-8-sig") as f:
            for r in csv.DictReader(f):
                if r.get("scene") == "1" and r.get("review") != "違う":
                    out.setdefault(r["clip"], []).append([float(r["raw_start"]), float(r["raw_end"])])
    return out


PAGE = """<!DOCTYPE html>
<html lang="ja"><head><meta charset="utf-8"><title>空中のコマに印を付ける</title>
<style>
body{{font-family:'Hiragino Sans','Yu Gothic UI',sans-serif;margin:0;padding:16px 20px 140px;color:#212529;background:#fff}}
h1{{font-size:20px;margin:0 0 6px}} h2{{font-size:15px;margin:22px 0 6px}} .m{{color:#64707C;font-size:13px;line-height:1.7}}
.g{{display:grid;grid-template-columns:repeat(10,1fr);gap:3px}}
.f{{position:relative;cursor:pointer;user-select:none}}
.f img{{width:100%;display:block;border-radius:3px}}
.f .t{{position:absolute;left:2px;bottom:2px;font-size:10px;background:rgba(0,0,0,.55);color:#fff;padding:0 3px;border-radius:2px}}
.f.o .t{{background:rgba(37,99,235,.85)}}
.f.air img{{outline:4px solid #DC2626;outline-offset:-4px}}
.f.air::after{{content:'空中';position:absolute;right:2px;top:2px;font-size:11px;background:#DC2626;color:#fff;padding:0 4px;border-radius:2px}}
.f.unk img{{outline:4px dashed #D97706;outline-offset:-4px}}
.f.unk::after{{content:'迷う';position:absolute;right:2px;top:2px;font-size:11px;background:#D97706;color:#fff;padding:0 4px;border-radius:2px}}
.bar{{position:fixed;bottom:0;left:0;right:0;background:#fff;border-top:1px solid #DEE2E6;padding:10px 20px}}
button{{background:#2563EB;color:#fff;border:0;border-radius:8px;padding:8px 20px;font-size:14px;cursor:pointer}}
textarea{{width:100%;height:90px;display:none;margin-top:6px;font-family:monospace}}
.nav{{font-size:12px;line-height:1.9}} .nav a{{margin-right:8px}}
</style></head><body>
<h1>空中のコマに印を付ける（{n_clips}本・{n_frames}コマ）</h1>
<p class="m">0.5秒ごとのコマを、動画ごとに1行10コマ（5秒分）で並べています。
<b>ライダーが水面を離れているコマ</b>をクリックすると「空中」（赤枠）になります。もう一度クリックで「迷う」（橙の点線）、もう一度で印なしに戻ります。<br>
<b>Shift を押しながらクリック</b>すると、直前にクリックしたコマからそこまでを同じ印でまとめて塗ります。画像を大きく見たいときは右クリック→「新しいタブで画像を開く」。<br>
小さなジャンプも「空中」にしてください。遠すぎて分からない、水しぶきで見えないコマは「迷う」に。トリック区間の前後1秒は後で自動で付けるので、印は空中のコマだけで構いません。<br>
時刻が<span style="background:#2563EB;color:#fff;padding:0 3px">青</span>のコマは、オーナーが完成動画にトリックとして使った区間です（探す手がかりで、正解ではありません）。入力はブラウザに自動で保存されます。</p>
<div class="nav">{nav}</div>
{sections}
<div class="bar"><button onclick="gen()">印の一覧を作る</button> <span id="st" class="m"></span>
<textarea id="out" readonly onclick="this.select()"></textarea></div>
<script>
var KEY="trick-air-label-v1",ST={{}},last=null;
try{{ST=JSON.parse(localStorage.getItem(KEY)||'{{}}');}}catch(e){{}}
function cls(el){{var v=ST[el.dataset.id]||0;el.classList.toggle('air',v===1);el.classList.toggle('unk',v===2);}}
function setv(el,v){{if(v)ST[el.dataset.id]=v;else delete ST[el.dataset.id];cls(el);}}
document.querySelectorAll('.f').forEach(function(el){{cls(el);el.addEventListener('click',function(ev){{
  var v;if(ev.shiftKey&&last&&last.dataset.clip===el.dataset.clip){{v=ST[last.dataset.id]||0;var fs=[].slice.call(document.querySelectorAll('.f[data-clip="'+el.dataset.clip+'"]'));
    var a=fs.indexOf(last),b=fs.indexOf(el);if(a>b){{var t=a;a=b;b=t;}}for(var i=a;i<=b;i++)setv(fs[i],v);}}
  else{{v=((ST[el.dataset.id]||0)+1)%3;setv(el,v);}}
  last=el;try{{localStorage.setItem(KEY,JSON.stringify(ST));}}catch(e){{}}count();}});}});
function count(){{var a=0,u=0;Object.keys(ST).forEach(function(k){{if(ST[k]===1)a++;else if(ST[k]===2)u++;}});document.getElementById('st').textContent='空中 '+a+'コマ・迷う '+u+'コマ';}}
function runs(list){{var out=[],s=null,p=null;list.forEach(function(t){{if(s===null){{s=t;p=t;}}else if(Math.abs(t-p-0.5)<1e-6){{p=t;}}else{{out.push(s===p?s.toFixed(1):s.toFixed(1)+'-'+p.toFixed(1));s=t;p=t;}}}});if(s!==null)out.push(s===p?s.toFixed(1):s.toFixed(1)+'-'+p.toFixed(1));return out.join(',');}}
function gen(){{var by={{}};document.querySelectorAll('.f').forEach(function(el){{var v=ST[el.dataset.id];if(!v)return;var c=el.dataset.clip;by[c]=by[c]||{{1:[],2:[]}};by[c][v].push(parseFloat(el.dataset.t));}});
  var lines=['【空中の印】'];Object.keys(by).sort().forEach(function(c){{var s=c+': '+(by[c][1].length?runs(by[c][1]):'-');if(by[c][2].length)s+=' / 迷う '+runs(by[c][2]);lines.push(s);}});
  if(lines.length===1)lines.push('印なし');var o=document.getElementById('out');o.value=lines.join('\\n');o.style.display='block';o.select();
  try{{navigator.clipboard.writeText(o.value);document.getElementById('st').textContent='コピーしました。チャットに貼ってください。';}}catch(e){{}}}}
count();
</script></body></html>"""


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("day_dir", type=Path)
    args = ap.parse_args()

    out_dir = args.day_dir / "eval" / "label_page"
    out_dir.mkdir(parents=True, exist_ok=True)
    videos = sorted(p for p in (args.day_dir / "raw").iterdir() if p.suffix.lower() in {".mp4", ".mov", ".m4v"})
    owner = load_owner(args.day_dir)

    sections, nav, total = [], [], 0
    for i, v in enumerate(videos, 1):
        names = extract(v, out_dir)
        total += len(names)
        cells = []
        for k, name in enumerate(names):
            t = k / FPS
            o = any(s - 0.25 <= t <= e + 0.25 for s, e in owner.get(v.name, []))
            cells.append(f'<div class="f{" o" if o else ""}" data-clip="{html.escape(v.name)}" data-t="{t:.1f}" '
                         f'data-id="{html.escape(v.name)}|{t:.1f}"><img src="{name}" loading="lazy">'
                         f'<span class="t">{t:.1f}</span></div>')
        anchor = f"v{i}"
        nav.append(f'<a href="#{anchor}">{i}. {html.escape(v.stem[9:])}</a>')
        sections.append(f'<h2 id="{anchor}">{i}. {html.escape(v.name)}（{len(names) / FPS:.0f}秒）</h2>'
                        f'<div class="g">{"".join(cells)}</div>')
        print(f"[{i}/{len(videos)}] {v.name} {len(names)}コマ", flush=True)

    page = PAGE.format(n_clips=len(videos), n_frames=total, nav="".join(nav), sections="\n".join(sections))
    (out_dir / "label.html").write_text(page, encoding="utf-8")
    (out_dir / "frames.json").write_text(json.dumps({"fps": FPS, "frames": total}), encoding="utf-8")
    print(f"保存: {out_dir / 'label.html'}（{total}コマ）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
