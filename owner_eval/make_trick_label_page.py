"""元素材すべてを 0.5秒ごとのコマで並べ、場面の種類の印を付けるページを作る（評価用の開発ツール）。

モデルを「印を付けて学習する」方式で作るための、教師データ作りの入口。
元素材のどこがその場面かを人が全部の時刻で決め、それを正解にして学習・評価する。

--kind air（既定）：①トリック
  空中  トリックの踏み切り〜着水のコマ。小さなジャンプも含む。
  迷う  技でない宙づり、遠すぎる・水しぶきで見えないなど（学習にも評価にも使わない）。
  トリック区間（踏み切りの1秒前〜着水の1秒後）は印から機械的に作るので、ここでは付けない。
--kind person：③人物・笑顔
  人物  人が主役として写っているコマ（顔か表情が分かる大きさ）。
  笑顔  人物のうち、笑顔が分かるコマ。
  迷う  判断がつかないコマ（学習にも評価にも使わない）。

出力（<day>/eval/label_page/）：
  <動画名>_NNNN.jpg  動画のコマ（NNNN 番目＝ NNNN×0.5 秒、幅320。両方の種類で共用）
  label.html / label_person.html  印付けページ（ブラウザで開く。外部への通信なし。入力はブラウザ内に自動保存）

使い方：
  .venv/Scripts/python.exe -I owner_eval/make_trick_label_page.py dataset/owner/2026-10-07_10 [--kind person]
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

KINDS = {
    "air": {
        "file": "label.html", "key": "trick-air-label-v1", "scene": "1", "header": "【空中の印】",
        "title": "空中のコマに印を付ける", "hint": "トリック",
        "states": [("空中", "#DC2626", "solid"), ("迷う", "#D97706", "dashed")],
        "rules": "<b>トリックの踏み切りから着水まで</b>のコマに「空中」を付けてください。小さなジャンプも含みます。"
                 "技でない宙づり（転倒で引き上げられた等）や、遠すぎる・水しぶきで見えないコマは「迷う」に。"
                 "着水ミスは着水の瞬間までを「空中」にします。トリック区間の前後1秒は後で自動で付けるので、印は空中のコマだけで構いません。",
    },
    "person": {
        "file": "label_person.html", "key": "person-label-v1", "scene": "3", "header": "【人物の印】",
        "title": "人物・笑顔のコマに印を付ける", "hint": "人物・笑顔",
        "states": [("人物", "#7C3AED", "solid"), ("笑顔", "#DB2777", "solid"), ("迷う", "#D97706", "dashed")],
        "rules": "<b>人が主役として写っているコマ</b>（顔か表情が分かる大きさ）に「人物」を、そのうち<b>笑顔が分かるコマ</b>には「笑顔」を付けてください。"
                 "遠くの小さなライダーや、背中だけで誰か分からない人は印なしです。判断がつかないコマは「迷う」に。",
    },
}


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


def load_owner(day_dir: Path, scene: str) -> dict[str, list[list[float]]]:
    """オーナーがその場面として使った区間（見つける手がかりとして表示するだけ。正解ではない）。"""
    out: dict[str, list[list[float]]] = {}
    path = day_dir / "match" / "segments_reviewed.csv"
    if path.exists():
        with path.open(encoding="utf-8-sig") as f:
            for r in csv.DictReader(f):
                if r.get("scene") == scene and r.get("review") != "違う":
                    out.setdefault(r["clip"], []).append([float(r["raw_start"]), float(r["raw_end"])])
    return out


PAGE = """<!DOCTYPE html>
<html lang="ja"><head><meta charset="utf-8"><title>{title}</title>
<style>
body{{font-family:'Hiragino Sans','Yu Gothic UI',sans-serif;margin:0;padding:16px 20px 140px;color:#212529;background:#fff}}
h1{{font-size:20px;margin:0 0 6px}} h2{{font-size:15px;margin:22px 0 6px}} .m{{color:#64707C;font-size:13px;line-height:1.7}}
.g{{display:grid;grid-template-columns:repeat(10,1fr);gap:3px}}
.f{{position:relative;cursor:pointer;user-select:none}}
.f img{{width:100%;display:block;border-radius:3px}}
.f .t{{position:absolute;left:2px;bottom:2px;font-size:10px;background:rgba(0,0,0,.55);color:#fff;padding:0 3px;border-radius:2px}}
.f.o .t{{background:rgba(37,99,235,.85)}}
.f .b{{position:absolute;right:2px;top:2px;font-size:11px;color:#fff;padding:0 4px;border-radius:2px;display:none}}
{state_css}
.bar{{position:fixed;bottom:0;left:0;right:0;background:#fff;border-top:1px solid #DEE2E6;padding:10px 20px}}
button{{background:#2563EB;color:#fff;border:0;border-radius:8px;padding:8px 20px;font-size:14px;cursor:pointer}}
textarea{{width:100%;height:90px;display:none;margin-top:6px;font-family:monospace}}
.nav{{font-size:12px;line-height:1.9}} .nav a{{margin-right:8px}}
</style></head><body>
<h1>{title}（{n_clips}本・{n_frames}コマ）</h1>
<p class="m">0.5秒ごとのコマを、動画ごとに1行10コマ（5秒分）で並べています。
コマをクリックするたびに印が {cycle} → 印なし の順に変わります。<br>
<b>Shift を押しながらクリック</b>すると、直前にクリックしたコマからそこまでを同じ印でまとめて塗ります。画像を大きく見たいときは右クリック→「新しいタブで画像を開く」。<br>
{rules}<br>
時刻が<span style="background:#2563EB;color:#fff;padding:0 3px">青</span>のコマは、オーナーが完成動画に{hint}として使った区間です（探す手がかりで、正解ではありません）。入力はブラウザに自動で保存されます。</p>
<div class="nav">{nav}</div>
{sections}
<div class="bar"><button onclick="gen()">印の一覧を作る</button> <span id="st" class="m"></span>
<textarea id="out" readonly onclick="this.select()"></textarea></div>
<script>
var KEY="{key}",NAMES={names},HEADER="{header}",ST={{}},last=null,N=NAMES.length;
try{{ST=JSON.parse(localStorage.getItem(KEY)||'{{}}');}}catch(e){{}}
function cls(el){{var v=ST[el.dataset.id]||0;for(var i=1;i<=N;i++)el.classList.toggle('s'+i,v===i);var b=el.querySelector('.b');b.textContent=v?NAMES[v-1]:'';}}
function setv(el,v){{if(v)ST[el.dataset.id]=v;else delete ST[el.dataset.id];cls(el);}}
document.querySelectorAll('.f').forEach(function(el){{cls(el);el.addEventListener('click',function(ev){{
  var v;if(ev.shiftKey&&last&&last.dataset.clip===el.dataset.clip){{v=ST[last.dataset.id]||0;var fs=[].slice.call(document.querySelectorAll('.f[data-clip="'+el.dataset.clip+'"]'));
    var a=fs.indexOf(last),b=fs.indexOf(el);if(a>b){{var t=a;a=b;b=t;}}for(var i=a;i<=b;i++)setv(fs[i],v);}}
  else{{v=((ST[el.dataset.id]||0)+1)%(N+1);setv(el,v);}}
  last=el;try{{localStorage.setItem(KEY,JSON.stringify(ST));}}catch(e){{}}count();}});}});
function count(){{var c=new Array(N+1).fill(0);Object.keys(ST).forEach(function(k){{c[ST[k]]++;}});document.getElementById('st').textContent=NAMES.map(function(n,i){{return n+' '+c[i+1]+'コマ';}}).join('・');}}
function runs(list){{var out=[],s=null,p=null;list.forEach(function(t){{if(s===null){{s=t;p=t;}}else if(Math.abs(t-p-0.5)<1e-6){{p=t;}}else{{out.push(s===p?s.toFixed(1):s.toFixed(1)+'-'+p.toFixed(1));s=t;p=t;}}}});if(s!==null)out.push(s===p?s.toFixed(1):s.toFixed(1)+'-'+p.toFixed(1));return out.join(',');}}
function gen(){{var by={{}};document.querySelectorAll('.f').forEach(function(el){{var v=ST[el.dataset.id];if(!v)return;var c=el.dataset.clip;by[c]=by[c]||NAMES.map(function(){{return [];}});by[c][v-1].push(parseFloat(el.dataset.t));}});
  var lines=[HEADER];Object.keys(by).sort().forEach(function(c){{var s=c+': '+(by[c][0].length?runs(by[c][0]):'-');for(var i=1;i<N;i++)if(by[c][i].length)s+=' / '+NAMES[i]+' '+runs(by[c][i]);lines.push(s);}});
  if(lines.length===1)lines.push('印なし');var o=document.getElementById('out');o.value=lines.join('\\n');o.style.display='block';o.select();
  try{{navigator.clipboard.writeText(o.value);document.getElementById('st').textContent='コピーしました。チャットに貼ってください。';}}catch(e){{}}}}
count();
</script></body></html>"""


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("day_dir", type=Path)
    ap.add_argument("--kind", choices=sorted(KINDS), default="air")
    args = ap.parse_args()
    kind = KINDS[args.kind]

    out_dir = args.day_dir / "eval" / "label_page"
    out_dir.mkdir(parents=True, exist_ok=True)
    videos = sorted(p for p in (args.day_dir / "raw").iterdir() if p.suffix.lower() in {".mp4", ".mov", ".m4v"})
    owner = load_owner(args.day_dir, kind["scene"])

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
                         f'<span class="t">{t:.1f}</span><span class="b"></span></div>')
        anchor = f"v{i}"
        nav.append(f'<a href="#{anchor}">{i}. {html.escape(v.stem[9:])}</a>')
        sections.append(f'<h2 id="{anchor}">{i}. {html.escape(v.name)}（{len(names) / FPS:.0f}秒）</h2>'
                        f'<div class="g">{"".join(cells)}</div>')
        print(f"[{i}/{len(videos)}] {v.name} {len(names)}コマ", flush=True)

    state_css = "\n".join(
        f".f.s{i} img{{outline:4px {style} {color};outline-offset:-4px}} .f.s{i} .b{{display:block;background:{color}}}"
        for i, (_, color, style) in enumerate(kind["states"], 1))
    names_js = json.dumps([n for n, _, _ in kind["states"]], ensure_ascii=False)
    page = PAGE.format(title=kind["title"], n_clips=len(videos), n_frames=total, nav="".join(nav),
                       sections="\n".join(sections), state_css=state_css, key=kind["key"], names=names_js,
                       header=kind["header"], rules=kind["rules"], hint=kind["hint"],
                       cycle=" → ".join(n for n, _, _ in kind["states"]))
    (out_dir / kind["file"]).write_text(page, encoding="utf-8")
    (out_dir / "frames.json").write_text(json.dumps({"fps": FPS, "frames": total}), encoding="utf-8")
    print(f"保存: {out_dir / kind['file']}（{total}コマ）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
