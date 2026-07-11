from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from sklearn.model_selection import GroupKFold

from clip_embedder import ClipEmbedder
from frame_scorer import (
    CLIP_CACHE_RELATIVE_DIR,
    CLIP_MODEL_RELATIVE_PATH,
    DEFAULT_AMBIGUOUS_SEC,
    dedupe_dataset_rows,
    exclude_ambiguous_negatives,
    make_clip_model,
    resolve_clip_targets,
    resolve_path,
    resolve_root,
)

# Phase 2(区間提案)のシミュレーション用スクリプトです。
# frame_scorer.py の学習ロジック(CLIP埋め込み + LogisticRegression, GroupKFold)を
# そのまま再利用し、フレーム単位のスコアから「区間提案」を組み立てたときの
# 精度(区間再現率・区間適合率)をオフラインで検証します。
# 本番運用のモデル(models/frame_scorer.pkl)には一切書き込みません。

DEFAULT_GAP = 2.0
DEFAULT_TOLERANCE = 1.0
DEFAULT_ENTER = 0.5
DEFAULT_EXIT = 0.4
DEFAULT_SMOOTH_WINDOW = 3

SWEEP_ENTER_VALUES = [0.45, 0.5, 0.55]
SWEEP_EXIT_VALUES = [0.35, 0.4, 0.45]
SWEEP_WINDOW_VALUES = [1, 3, 5]

REPORT_RELATIVE_PATH = Path("logs/segment_sim_report.txt")
CSV_RELATIVE_PATH = Path("logs/segment_sim.csv")


@dataclass
class FrameRecord:
    """突き合わせに必要な最小限のフレーム情報(1フレーム分)。"""

    video_stem: str
    time_sec: float
    label: int
    oof_score: float


Segment = tuple[float, float]  # (開始時刻, 終了時刻)


def load_usable_frames(root: Path) -> tuple[list[dict[str, str]], int, int]:
    """dataset/labels.csv を読み込み、重複排除・画像パス解決を行います。

    戻り値は (画像を解決できた行のリスト, 重複排除前の行数, 画像未解決で除外した件数)。
    """
    dataset_path = root / "dataset" / "labels.csv"
    if not dataset_path.exists():
        raise FileNotFoundError(f"学習データが見つかりません: {dataset_path}")

    with dataset_path.open("r", encoding="utf-8-sig", newline="") as file:
        raw_rows = list(csv.DictReader(file))

    deduped = dedupe_dataset_rows(raw_rows)
    image_root = dataset_path.parent
    valid_indices, valid_paths = resolve_clip_targets(deduped, image_root)
    usable_rows = [deduped[index] for index in valid_indices]
    unresolved_count = len(deduped) - len(usable_rows)

    if unresolved_count:
        print(
            f"警告: image_path が解決できない行を {unresolved_count} 件除外しました"
            "(image_pathが空、または画像ファイルが存在しません)"
        )

    # usable_rows と valid_paths は同じ順序で対応しています。
    for row, path in zip(usable_rows, valid_paths):
        row["_resolved_image_path"] = str(path)

    return usable_rows, len(deduped), unresolved_count


def compute_oof_scores(
    root: Path, usable_rows: list[dict[str, str]], ambiguous_sec: float
) -> np.ndarray:
    """GroupKFold(動画単位)でout-of-fold予測確率を計算します。

    学習側のみ曖昧ネガティブ(±ambiguous_sec秒)を除外し、予測はfold内の
    全フレーム(曖昧ネガティブを含む)に対して行います(本番運用と同じ条件)。
    """
    clip_model_path = root / CLIP_MODEL_RELATIVE_PATH
    if not clip_model_path.exists():
        raise FileNotFoundError(f"CLIPモデルが見つかりません: {clip_model_path}")

    clip_cache_dir = root / CLIP_CACHE_RELATIVE_DIR
    embedder = ClipEmbedder(model_path=clip_model_path, cache_dir=clip_cache_dir)
    image_paths = [Path(row["_resolved_image_path"]) for row in usable_rows]
    X = embedder.embed(image_paths).astype(float)

    y = np.array([1 if (row.get("label") or "") == "1" else 0 for row in usable_rows], dtype=int)
    groups = np.array([row.get("video_stem", "") for row in usable_rows])

    unique_groups = sorted(set(groups.tolist()))
    if len(unique_groups) < 2:
        raise ValueError(f"動画本数が{len(unique_groups)}本しかなく、GroupKFold評価ができません。")

    n_splits = min(5, len(unique_groups))
    gkf = GroupKFold(n_splits=n_splits)

    oof_proba = np.zeros(len(usable_rows), dtype=float)
    total_ambiguous_excluded = 0

    for train_idx, test_idx in gkf.split(X, y, groups=groups):
        # 曖昧ネガティブ除外は学習側のみに適用するため、行に元インデックスを
        # 一時的に付与してから frame_scorer.exclude_ambiguous_negatives に渡します。
        train_rows_with_index = []
        for index in train_idx:
            row_copy = dict(usable_rows[int(index)])
            row_copy["_fold_index"] = int(index)
            train_rows_with_index.append(row_copy)

        filtered_train_rows, excluded = exclude_ambiguous_negatives(train_rows_with_index, ambiguous_sec)
        total_ambiguous_excluded += excluded
        filtered_train_indices = np.array([row["_fold_index"] for row in filtered_train_rows], dtype=int)

        model = make_clip_model()
        model.fit(X[filtered_train_indices], y[filtered_train_indices])
        oof_proba[test_idx] = model.predict_proba(X[test_idx])[:, 1]

    print(f"曖昧ネガティブ除外(学習側のみ、延べfold合計): {total_ambiguous_excluded} 件")
    return oof_proba


def build_frame_records(usable_rows: list[dict[str, str]], oof_proba: np.ndarray) -> list[FrameRecord]:
    records: list[FrameRecord] = []
    for row, score in zip(usable_rows, oof_proba):
        records.append(
            FrameRecord(
                video_stem=row.get("video_stem", ""),
                time_sec=float(row.get("time_sec") or 0.0),
                label=1 if (row.get("label") or "") == "1" else 0,
                oof_score=float(score),
            )
        )
    return records


def group_records_by_video(records: list[FrameRecord]) -> dict[str, list[FrameRecord]]:
    by_video: dict[str, list[FrameRecord]] = {}
    for record in records:
        by_video.setdefault(record.video_stem, []).append(record)
    for video_stem, video_records in by_video.items():
        video_records.sort(key=lambda item: item.time_sec)
    return by_video


def build_true_segments(video_records: list[FrameRecord], gap: float) -> list[Segment]:
    """label=1のフレームのtime_secを昇順に並べ、隣接間隔がgapを超えたら区切ります
    (kite_clipper.py の group_selected_frames と同じ考え方)。"""
    positive_times = sorted(record.time_sec for record in video_records if record.label == 1)
    if not positive_times:
        return []
    gap = max(0.0, gap)
    groups: list[list[float]] = [[positive_times[0]]]
    for time_sec in positive_times[1:]:
        if time_sec - groups[-1][-1] <= gap:
            groups[-1].append(time_sec)
        else:
            groups.append([time_sec])
    return [(group[0], group[-1]) for group in groups]


def smooth_scores(scores: list[float], window: int) -> list[float]:
    """移動平均で平滑化します(窓=window、中央寄せ)。window<=1の場合は平滑化なし。"""
    n = len(scores)
    if window <= 1 or n == 0:
        return list(scores)
    half = window // 2
    smoothed: list[float] = []
    for i in range(n):
        lo = max(0, i - half)
        hi = min(n, i + half + 1)
        smoothed.append(sum(scores[lo:hi]) / (hi - lo))
    return smoothed


def build_proposed_segments(
    video_records: list[FrameRecord], enter: float, exit_: float, window: int
) -> list[Segment]:
    """ヒステリシス(enter/exit)で提案区間を作ります。
    平滑化スコアがenter以上で区間開始、exit未満で区間終了(区間は[開始, 終了直前]の時刻)。"""
    if not video_records:
        return []
    times = [record.time_sec for record in video_records]
    raw_scores = [record.oof_score for record in video_records]
    scores = smooth_scores(raw_scores, window)

    segments: list[Segment] = []
    in_segment = False
    start_time = 0.0
    last_high_time = 0.0
    for time_sec, score in zip(times, scores):
        if not in_segment:
            if score >= enter:
                in_segment = True
                start_time = time_sec
                last_high_time = time_sec
        else:
            if score >= exit_:
                last_high_time = time_sec
            else:
                segments.append((start_time, last_high_time))
                in_segment = False
    if in_segment:
        segments.append((start_time, last_high_time))
    return segments


def expand(segment: Segment, tolerance: float) -> Segment:
    return (segment[0] - tolerance, segment[1] + tolerance)


def overlaps(a: Segment, b: Segment) -> bool:
    return a[0] <= b[1] and b[0] <= a[1]


@dataclass
class VideoMatchResult:
    video_stem: str
    true_segments: list[Segment]
    proposed_segments: list[Segment]
    matched_true_flags: list[bool]
    matched_proposed_flags: list[bool]


def match_video(
    video_stem: str, true_segments: list[Segment], proposed_segments: list[Segment], tolerance: float
) -> VideoMatchResult:
    true_expanded = [expand(segment, tolerance) for segment in true_segments]
    proposed_expanded = [expand(segment, tolerance) for segment in proposed_segments]

    matched_true_flags = [
        any(overlaps(true_expanded[i], proposed_expanded[j]) for j in range(len(proposed_expanded)))
        for i in range(len(true_segments))
    ]
    matched_proposed_flags = [
        any(overlaps(proposed_expanded[j], true_expanded[i]) for i in range(len(true_segments)))
        for j in range(len(proposed_segments))
    ]
    return VideoMatchResult(
        video_stem=video_stem,
        true_segments=true_segments,
        proposed_segments=proposed_segments,
        matched_true_flags=matched_true_flags,
        matched_proposed_flags=matched_proposed_flags,
    )


@dataclass
class EvaluationResult:
    enter: float
    exit_: float
    window: int
    gap: float
    tolerance: float
    segment_recall: float
    segment_precision: float
    f1: float
    n_proposed_total: int
    n_true_total: int
    frame_recall: float
    video_results: list[VideoMatchResult]


def evaluate_params(
    records_by_video: dict[str, list[FrameRecord]],
    gap: float,
    tolerance: float,
    enter: float,
    exit_: float,
    window: int,
) -> EvaluationResult:
    video_results: list[VideoMatchResult] = []
    n_true_total = 0
    n_matched_true_total = 0
    n_proposed_total = 0
    n_matched_proposed_total = 0
    n_positive_frames_total = 0
    n_positive_frames_covered = 0

    for video_stem, video_records in records_by_video.items():
        true_segments = build_true_segments(video_records, gap)
        proposed_segments = build_proposed_segments(video_records, enter, exit_, window)
        result = match_video(video_stem, true_segments, proposed_segments, tolerance)
        video_results.append(result)

        n_true_total += len(true_segments)
        n_matched_true_total += sum(result.matched_true_flags)
        n_proposed_total += len(proposed_segments)
        n_matched_proposed_total += sum(result.matched_proposed_flags)

        for record in video_records:
            if record.label != 1:
                continue
            n_positive_frames_total += 1
            if any(seg[0] <= record.time_sec <= seg[1] for seg in proposed_segments):
                n_positive_frames_covered += 1

    segment_recall = (n_matched_true_total / n_true_total) if n_true_total else 0.0
    segment_precision = (n_matched_proposed_total / n_proposed_total) if n_proposed_total else 0.0
    if segment_recall + segment_precision > 0:
        f1 = 2 * segment_recall * segment_precision / (segment_recall + segment_precision)
    else:
        f1 = 0.0
    frame_recall = (n_positive_frames_covered / n_positive_frames_total) if n_positive_frames_total else 0.0

    return EvaluationResult(
        enter=enter,
        exit_=exit_,
        window=window,
        gap=gap,
        tolerance=tolerance,
        segment_recall=segment_recall,
        segment_precision=segment_precision,
        f1=f1,
        n_proposed_total=n_proposed_total,
        n_true_total=n_true_total,
        frame_recall=frame_recall,
        video_results=video_results,
    )


def build_sweep_table_lines(results: list[EvaluationResult], default_params: tuple[float, float, int]) -> list[str]:
    lines: list[str] = []
    header = f"{'enter':>6} {'exit':>6} {'窓':>4} {'区間再現率':>10} {'区間適合率':>10} {'F1':>8} {'提案数':>8}  備考"
    lines.append(header)
    sorted_results = sorted(results, key=lambda r: r.f1, reverse=True)
    for result in sorted_results:
        is_default = (result.enter, result.exit_, result.window) == default_params
        note = "<- 既定パラメータ" if is_default else ""
        lines.append(
            f"{result.enter:>6.2f} {result.exit_:>6.2f} {result.window:>4d} "
            f"{result.segment_recall:>10.4f} {result.segment_precision:>10.4f} {result.f1:>8.4f} "
            f"{result.n_proposed_total:>8d}  {note}"
        )
    return lines


def build_detail_lines(result: EvaluationResult, max_examples: int = 5) -> list[str]:
    lines: list[str] = []
    lines.append(
        f"パラメータ: enter={result.enter}, exit={result.exit_}, window={result.window}, "
        f"gap={result.gap}, tolerance={result.tolerance}"
    )
    lines.append(
        f"区間再現率: {result.segment_recall:.4f} / 区間適合率: {result.segment_precision:.4f} / "
        f"F1: {result.f1:.4f}"
    )
    lines.append(f"提案区間総数: {result.n_proposed_total} / 正解区間総数: {result.n_true_total}")
    lines.append(f"フレーム単位のrecall(補助指標): {result.frame_recall:.4f}")

    missed_examples: list[str] = []
    n_missed = 0
    unmatched_proposed = 0
    for video_result in result.video_results:
        for segment, matched in zip(video_result.true_segments, video_result.matched_true_flags):
            if not matched:
                n_missed += 1
                if len(missed_examples) < max_examples:
                    missed_examples.append(
                        f"{video_result.video_stem}: {segment[0]:.2f}s - {segment[1]:.2f}s"
                    )
        unmatched_proposed += sum(1 for matched in video_result.matched_proposed_flags if not matched)

    lines.append("")
    lines.append(f"取りこぼした正解区間: {n_missed} 件")
    for example in missed_examples:
        lines.append(f"  例: {example}")
    lines.append(f"正解に当たらなかった提案区間: {unmatched_proposed} 件")
    return lines


def write_csv(csv_path: Path, results: list[EvaluationResult]) -> None:
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    with csv_path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.writer(file)
        writer.writerow(
            [
                "enter",
                "exit",
                "window",
                "gap",
                "tolerance",
                "segment_recall",
                "segment_precision",
                "f1",
                "n_proposed_total",
                "n_true_total",
                "frame_recall",
            ]
        )
        for result in results:
            writer.writerow(
                [
                    result.enter,
                    result.exit_,
                    result.window,
                    result.gap,
                    result.tolerance,
                    f"{result.segment_recall:.4f}",
                    f"{result.segment_precision:.4f}",
                    f"{result.f1:.4f}",
                    result.n_proposed_total,
                    result.n_true_total,
                    f"{result.frame_recall:.4f}",
                ]
            )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Phase 2(区間提案)のヒステリシス方式を、frame_scorer.py と同じCLIP+"
        "LogisticRegressionのout-of-fold予測でシミュレーションします。"
    )
    parser.add_argument(
        "--gap", type=float, default=DEFAULT_GAP, help=f"正解区間を区切る間隔(秒)（既定: {DEFAULT_GAP}）"
    )
    parser.add_argument(
        "--tolerance",
        type=float,
        default=DEFAULT_TOLERANCE,
        help=f"区間の突き合わせ時に前後に拡張する許容秒数（既定: {DEFAULT_TOLERANCE}）",
    )
    parser.add_argument(
        "--enter", type=float, default=DEFAULT_ENTER, help=f"区間開始のスコアしきい値（既定: {DEFAULT_ENTER}）"
    )
    parser.add_argument(
        "--exit", dest="exit_", type=float, default=DEFAULT_EXIT, help=f"区間終了のスコアしきい値（既定: {DEFAULT_EXIT}）"
    )
    parser.add_argument(
        "--smooth-window",
        type=int,
        default=DEFAULT_SMOOTH_WINDOW,
        help=f"スコア平滑化の移動平均窓(フレーム数、中央寄せ。1で平滑化なし)（既定: {DEFAULT_SMOOTH_WINDOW}）",
    )
    parser.add_argument(
        "--no-sweep",
        action="store_true",
        help="パラメータスイープを行わず、--enter/--exit/--smooth-windowで指定した設定のみ評価します",
    )
    parser.add_argument(
        "--root", type=Path, default=None, help="プロジェクトルート（既定はこのスクリプトのあるフォルダ）"
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    root = resolve_root(args.root)

    if args.enter <= args.exit_:
        print(f"エラー: --enter({args.enter})は--exit({args.exit_})より大きい必要があります。")
        return 1

    try:
        usable_rows, n_deduped, unresolved_count = load_usable_frames(root)
    except FileNotFoundError as exc:
        print(f"エラー: {exc}")
        return 1

    if not usable_rows:
        print("エラー: 評価対象のフレームが0件です(image_pathが解決できる行がありません)。")
        return 1

    try:
        oof_proba = compute_oof_scores(root, usable_rows, DEFAULT_AMBIGUOUS_SEC)
    except (FileNotFoundError, ValueError) as exc:
        print(f"エラー: {exc}")
        return 1

    records = build_frame_records(usable_rows, oof_proba)
    records_by_video = group_records_by_video(records)

    n_videos = len(records_by_video)
    n_frames = len(records)
    default_params = (DEFAULT_ENTER, DEFAULT_EXIT, DEFAULT_SMOOTH_WINDOW)

    report_lines: list[str] = []
    report_lines.append("=== segment_simulator レポート ===")
    report_lines.append(
        f"データ概要: 動画本数 {n_videos} / フレーム数 {n_frames}"
        f"(重複排除後 {n_deduped} 件のうち画像未解決 {unresolved_count} 件を除外)"
    )

    true_segments_by_video = {
        video_stem: build_true_segments(video_records, args.gap)
        for video_stem, video_records in records_by_video.items()
    }
    total_true_segments = sum(len(segments) for segments in true_segments_by_video.values())
    avg_true_segments = total_true_segments / n_videos if n_videos else 0.0
    report_lines.append(
        f"正解区間の総数: {total_true_segments} 件(動画あたり平均 {avg_true_segments:.2f} 件、gap={args.gap}秒)"
    )
    report_lines.append("")

    if args.no_sweep:
        result = evaluate_params(records_by_video, args.gap, args.tolerance, args.enter, args.exit_, args.smooth_window)
        report_lines.append("--- 指定パラメータの詳細(--no-sweep) ---")
        report_lines.extend(build_detail_lines(result))
        all_results = [result]
    else:
        all_results: list[EvaluationResult] = []
        for enter in SWEEP_ENTER_VALUES:
            for exit_ in SWEEP_EXIT_VALUES:
                if enter <= exit_:
                    continue
                for window in SWEEP_WINDOW_VALUES:
                    result = evaluate_params(records_by_video, args.gap, args.tolerance, enter, exit_, window)
                    all_results.append(result)

        report_lines.append(
            f"--- パラメータスイープ結果(F1降順、gap={args.gap}秒, tolerance={args.tolerance}秒) ---"
        )
        report_lines.extend(build_sweep_table_lines(all_results, default_params))
        report_lines.append("")

        best_result = max(all_results, key=lambda r: r.f1)
        report_lines.append("--- 最良パラメータの詳細 ---")
        report_lines.extend(build_detail_lines(best_result))

        default_result = next(
            (r for r in all_results if (r.enter, r.exit_, r.window) == default_params), None
        )
        if default_result is not None:
            report_lines.append("")
            report_lines.append("--- 既定パラメータ(enter=0.5, exit=0.4, window=3)の詳細 ---")
            report_lines.extend(build_detail_lines(default_result))

    report_text = "\n".join(report_lines)
    print(report_text)

    report_path = root / REPORT_RELATIVE_PATH
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(report_text + "\n", encoding="utf-8")

    csv_path = root / CSV_RELATIVE_PATH
    write_csv(csv_path, all_results)

    print("")
    print(f"レポート出力先: {report_path}")
    print(f"CSV出力先: {csv_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
