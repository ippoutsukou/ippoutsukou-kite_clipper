from __future__ import annotations

import argparse
import csv
import json
import re
import statistics
import sys
from datetime import datetime
from pathlib import Path

import joblib
import numpy as np
import sklearn
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import RandomForestClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import average_precision_score, precision_score, recall_score, roc_auc_score
from sklearn.model_selection import GroupKFold, cross_val_predict
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

from clip_embedder import CLIP_EMBED_DIM, ClipEmbedder

# kite_clipper.py / record_labels.py のフレーム命名規則と同じもの。frame_scorer.py は
# kite_clipper.py・record_labels.py からは完全に独立させるため、ここに複製しています
# （CLIP画像埋め込みの実装だけは同一プロジェクト内の clip_embedder.py を再利用します）。
FRAME_NAME_PATTERN = re.compile(r"^(?P<video_stem>.+)__t(?P<time>\d+(?:\.\d+)?)$")
PHOTO_EXTENSIONS = {".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff", ".webp"}

# logs/result.csv の列名 -> 特徴量名（record_labels.py の RESULT_FEATURE_COLUMNS と
# 同じ対応関係。frame_scorer.py 用に「特徴量名: result.csv列名」の向きで持ちます）
RESULT_FEATURE_SOURCE_COLUMNS = {
    "blur_score": "blur_score",
    "brightness": "brightness",
    "width": "width",
    "height": "height",
    "megapixels": "megapixels",
    "is_blurry": "is_blurry",
    "is_dark": "is_dark",
    "is_low_resolution": "is_low_resolution",
    "has_person_candidate": "has_person_candidate",
    "has_kite_candidate": "has_kite_candidate",
    "is_empty_scene": "is_empty_scene",
    "heuristic_score": "score",
}

# 学習・予測で共通して使う特徴量ベクトルの定義。
NUMERIC_FEATURES = ["blur_score", "brightness", "width", "height", "megapixels", "heuristic_score"]
BOOL_FEATURES = [
    "is_blurry",
    "is_dark",
    "is_low_resolution",
    "has_person_candidate",
    "has_kite_candidate",
    "is_empty_scene",
]
FEATURE_NAMES = NUMERIC_FEATURES + BOOL_FEATURES + ["audio_peak_value", "audio_peak_known"]

THRESHOLDS = [0.3, 0.4, 0.5, 0.6, 0.7]

# 方式B(CLIP画像埋め込み)関連の既定値。
FEATURE_MODE_CHOICES = ["classic", "clip", "both"]
DEFAULT_AMBIGUOUS_SEC = 1.5
CLIP_LOGREG_C = 0.1
CLIP_MODEL_RELATIVE_PATH = Path("vendor/clip/vision_model_quantized.onnx")
CLIP_CACHE_RELATIVE_DIR = Path("work/embeddings")


def resolve_root(root_arg: Path | None) -> Path:
    return root_arg.resolve() if root_arg else Path(__file__).resolve().parent


def resolve_path(root: Path, value: Path) -> Path:
    return value if value.is_absolute() else root / value


def compute_medians(rows: list[dict[str, str]]) -> dict[str, float]:
    """数値特徴量ごとに、空文字を除いた値の中央値を計算します（欠損補完用）。"""
    medians: dict[str, float] = {}
    for name in NUMERIC_FEATURES:
        values: list[float] = []
        for row in rows:
            text = (row.get(name) or "").strip()
            if text == "":
                continue
            try:
                values.append(float(text))
            except ValueError:
                continue
        medians[name] = float(statistics.median(values)) if values else 0.0
    return medians


def build_feature_row(raw: dict[str, str], medians: dict[str, float]) -> list[float]:
    """dataset/labels.csv 相当の文字列辞書1行分から、学習・予測共通の特徴量ベクトルを作ります。"""
    values: list[float] = []
    for name in NUMERIC_FEATURES:
        text = (raw.get(name) or "").strip()
        if text == "":
            values.append(medians.get(name, 0.0))
            continue
        try:
            values.append(float(text))
        except ValueError:
            values.append(medians.get(name, 0.0))
    for name in BOOL_FEATURES:
        text = (raw.get(name) or "").strip()
        values.append(1.0 if text == "True" else 0.0)
    audio_peak = (raw.get("audio_peak") or "").strip()
    if audio_peak == "1":
        values.append(1.0)
        values.append(1.0)
    elif audio_peak == "0":
        values.append(0.0)
        values.append(1.0)
    else:
        values.append(0.0)
        values.append(0.0)
    return values


def dedupe_dataset_rows(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    """同一 (video_stem, frame_name) が複数行ある場合、run_id が最大(文字列比較)の行だけ残します。"""
    latest: dict[tuple[str, str], dict[str, str]] = {}
    for row in rows:
        key = (row.get("video_stem", ""), row.get("frame_name", ""))
        current = latest.get(key)
        if current is None or (row.get("run_id", "") or "") > (current.get("run_id", "") or ""):
            latest[key] = row
    return list(latest.values())


def exclude_ambiguous_negatives(
    rows: list[dict[str, str]], ambiguous_sec: float
) -> tuple[list[dict[str, str]], int]:
    """同一動画内で、採用フレーム(label=1)の time_sec から ±ambiguous_sec 秒以内にある
    非採用フレーム(label=0)の行を学習・評価対象から除外します。

    ambiguous_sec が0以下の場合は何もせず、除外件数は0を返します。
    """
    if ambiguous_sec <= 0:
        return rows, 0

    positive_times: dict[str, list[float]] = {}
    for row in rows:
        if (row.get("label") or "") == "1":
            try:
                time_sec = float(row.get("time_sec") or 0.0)
            except ValueError:
                continue
            positive_times.setdefault(row.get("video_stem", ""), []).append(time_sec)

    kept: list[dict[str, str]] = []
    excluded = 0
    for row in rows:
        if (row.get("label") or "") == "1":
            kept.append(row)
            continue
        times = positive_times.get(row.get("video_stem", ""), [])
        try:
            time_sec = float(row.get("time_sec") or 0.0)
        except ValueError:
            kept.append(row)
            continue
        if times and any(abs(time_sec - positive_time) <= ambiguous_sec for positive_time in times):
            excluded += 1
            continue
        kept.append(row)
    return kept, excluded


def resolve_clip_targets(rows: list[dict[str, str]], image_root: Path) -> tuple[list[int], list[Path]]:
    """dataset行ごとにCLIP埋め込み対象の画像パスを解決します。

    image_path が空、または画像ファイルが存在しない行は対象外にします。
    戻り値は (rows内でのインデックス一覧, 対応する画像パス一覧) です。
    """
    valid_indices: list[int] = []
    valid_paths: list[Path] = []
    for index, row in enumerate(rows):
        rel = (row.get("image_path") or "").strip()
        if not rel:
            continue
        path = image_root / rel
        if not path.exists():
            continue
        valid_indices.append(index)
        valid_paths.append(path)
    return valid_indices, valid_paths


def make_classic_model() -> RandomForestClassifier:
    return RandomForestClassifier(
        n_estimators=300,
        min_samples_leaf=2,
        class_weight="balanced",
        random_state=42,
        n_jobs=-1,
    )


def make_clip_model() -> LogisticRegression:
    # 埋め込みはL2正規化済みのためスケーリング不要。
    return LogisticRegression(C=CLIP_LOGREG_C, class_weight="balanced", max_iter=2000)


def make_both_model(classic_dim: int) -> Pipeline:
    # classic特徴量(先頭classic_dim列)だけStandardScalerでスケーリングし、
    # CLIP埋め込み(残り512列)はそのまま連結してLogisticRegressionに渡します。
    preprocess = ColumnTransformer(
        [
            ("classic_scaled", StandardScaler(), list(range(classic_dim))),
            ("clip_passthrough", "passthrough", list(range(classic_dim, classic_dim + CLIP_EMBED_DIM))),
        ]
    )
    return Pipeline(
        [
            ("preprocess", preprocess),
            ("clf", LogisticRegression(C=CLIP_LOGREG_C, class_weight="balanced", max_iter=2000)),
        ]
    )


def cross_validate_variant(
    X: np.ndarray, y: np.ndarray, groups: np.ndarray, model_factory
) -> tuple[float, float, np.ndarray, int]:
    """GroupKFold(動画単位)でout-of-fold予測を作り、PR-AUC/ROC-AUCを計算します。

    動画本数が2本未満など評価不可能な場合は ValueError を送出します。
    """
    unique_groups = sorted(set(groups.tolist()))
    if len(unique_groups) < 2 or len(set(y.tolist())) < 2:
        raise ValueError("動画本数またはラベル種類が不足しているため評価できません")
    n_splits = min(5, len(unique_groups))
    cv = GroupKFold(n_splits=n_splits)
    model = model_factory()
    oof_proba = cross_val_predict(model, X, y, cv=cv, groups=groups, method="predict_proba")[:, 1]
    pr_auc = float(average_precision_score(y, oof_proba))
    roc_auc = float(roc_auc_score(y, oof_proba))
    return pr_auc, roc_auc, oof_proba, n_splits


def evaluate_variant(
    name: str,
    X: np.ndarray,
    y: np.ndarray,
    groups: np.ndarray,
    model_factory,
    skip_reason: str | None,
) -> tuple[dict[str, object], np.ndarray | None]:
    """特徴量セット1種類分の交差検証を実行し、比較表1行分の辞書とoof予測を返します。"""
    n_videos = len(set(groups.tolist())) if groups.size else 0
    if skip_reason is not None:
        return (
            {"features": name, "n_samples": int(X.shape[0]), "n_videos": n_videos, "skipped": True, "note": skip_reason},
            None,
        )
    try:
        pr_auc, roc_auc, oof_proba, _ = cross_validate_variant(X, y, groups, model_factory)
        return (
            {
                "features": name,
                "pr_auc": pr_auc,
                "roc_auc": roc_auc,
                "n_samples": int(X.shape[0]),
                "n_videos": n_videos,
                "skipped": False,
            },
            oof_proba,
        )
    except ValueError as exc:
        return (
            {"features": name, "n_samples": int(X.shape[0]), "n_videos": n_videos, "skipped": True, "note": str(exc)},
            None,
        )


def compute_importances(features_mode: str, model, classic_dim: int) -> list[tuple[str, float]]:
    """最終モデルの特徴量重要度を計算します(RandomForestは feature_importances_、
    LogisticRegression系は係数の絶対値を使用。CLIP512次元は平均値1本にまとめます)。"""
    if features_mode == "classic":
        return sorted(
            zip(FEATURE_NAMES, model.feature_importances_.tolist()), key=lambda item: item[1], reverse=True
        )
    if features_mode == "clip":
        coef = np.abs(model.coef_[0])
        return [("clip_embedding(512次元, 平均|係数|)", float(coef.mean()))]
    # both: Pipelineの最終ステップ(LogisticRegression)の係数を使う
    clf = model.named_steps["clf"]
    coef = np.abs(clf.coef_[0])
    classic_importances = list(zip(FEATURE_NAMES, coef[:classic_dim].tolist()))
    clip_importance = ("clip_embedding(512次元, 平均|係数|)", float(coef[classic_dim:].mean()))
    return sorted(classic_importances + [clip_importance], key=lambda item: item[1], reverse=True)


def load_result_features(csv_path: Path) -> dict[str, dict[str, str]]:
    """logs/result.csv の type=='frame' 行を output_path のファイル名(basename)をキーにして読み込みます。"""
    features: dict[str, dict[str, str]] = {}
    if not csv_path.exists():
        return features
    with csv_path.open("r", encoding="utf-8-sig", newline="") as file:
        for row in csv.DictReader(file):
            if row.get("type") != "frame":
                continue
            basename = Path(row.get("output_path") or "").name
            if not basename:
                continue
            features[basename] = {
                target: row.get(source, "") or "" for target, source in RESULT_FEATURE_SOURCE_COLUMNS.items()
            }
    return features


def find_latest_audio_peaks_file(work_audio_dir: Path, video_stem: str) -> Path | None:
    if not work_audio_dir.exists():
        return None
    candidates = sorted(
        work_audio_dir.glob(f"{video_stem}_*.json"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    return candidates[0] if candidates else None


def load_audio_peaks(path: Path) -> set[int]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return {int(value) for value in data}
    except Exception:
        return set()


class AudioPeakLookup:
    """動画ごとの音量ピーク秒集合を遅延読み込みしてキャッシュします(record_labels.py と同じ方式)。"""

    def __init__(self, work_audio_dir: Path) -> None:
        self.work_audio_dir = work_audio_dir
        self._cache: dict[str, set[int] | None] = {}

    def get(self, video_stem: str, time_sec: float) -> str:
        if video_stem not in self._cache:
            peaks_file = find_latest_audio_peaks_file(self.work_audio_dir, video_stem)
            self._cache[video_stem] = load_audio_peaks(peaks_file) if peaks_file else None
        peaks = self._cache[video_stem]
        if peaks is None:
            return ""
        second = int(time_sec)
        is_peak = second in peaks or (second - 1) in peaks or (second + 1) in peaks
        return "1" if is_peak else "0"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="dataset/labels.csv の教師データからフレーム採用スコアを学習・予測します。"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    train_parser = subparsers.add_parser(
        "train", help="dataset/labels.csv から学習し、モデルをmodels/に保存します"
    )
    train_parser.add_argument(
        "--dataset",
        type=Path,
        default=Path("dataset/labels.csv"),
        help="学習データCSVのパス（既定: dataset/labels.csv）",
    )
    train_parser.add_argument(
        "--model-out",
        type=Path,
        default=Path("models/frame_scorer.pkl"),
        help="保存するモデルファイルのパス（既定: models/frame_scorer.pkl）",
    )
    train_parser.add_argument(
        "--root", type=Path, default=None, help="プロジェクトルート（既定はこのスクリプトのあるフォルダ）"
    )
    train_parser.add_argument(
        "--features",
        choices=FEATURE_MODE_CHOICES,
        default="both",
        help="学習・保存する最終モデルの特徴量セット（classic/clip/both、既定: both）。"
        "レポートには常に3種類全ての比較結果を表示します。",
    )
    train_parser.add_argument(
        "--ambiguous-sec",
        type=float,
        default=DEFAULT_AMBIGUOUS_SEC,
        help="同一動画内で採用フレーム(label=1)の time_sec から±この秒数以内にある非採用フレーム"
        f"(label=0)を学習・評価から除外します（0以下で無効、既定: {DEFAULT_AMBIGUOUS_SEC}）",
    )

    predict_parser = subparsers.add_parser(
        "predict", help="review/frames のフレームにAIスコアを付与し、logs/ai_scores.csv に出力します"
    )
    predict_parser.add_argument(
        "--model",
        type=Path,
        default=Path("models/frame_scorer.pkl"),
        help="読み込むモデルファイルのパス（既定: models/frame_scorer.pkl）",
    )
    predict_parser.add_argument(
        "--out",
        type=Path,
        default=Path("logs/ai_scores.csv"),
        help="出力CSVのパス（既定: logs/ai_scores.csv）",
    )
    predict_parser.add_argument(
        "--root", type=Path, default=None, help="プロジェクトルート（既定はこのスクリプトのあるフォルダ）"
    )

    return parser.parse_args()


def build_train_report(
    counts: dict[str, int],
    ambiguous_sec: float,
    ambiguous_excluded: int,
    comparison_rows: list[dict[str, object]],
    selected_features: str,
    baseline: float,
    threshold_rows: list[dict[str, float]],
    importances: list[tuple[str, float]],
    model_out_path: Path,
    meta_path: Path,
    report_path: Path,
) -> list[str]:
    lines: list[str] = []
    lines.append("=== frame_scorer train レポート ===")
    lines.append(
        f"データ件数: 全体 {counts['total']} / 採用 {counts['adopted']} / "
        f"非採用 {counts['not_adopted']} / 動画本数 {counts['videos']}"
    )
    if ambiguous_sec > 0:
        lines.append(
            f"曖昧ネガティブ除外: {ambiguous_excluded} 件 "
            f"(採用フレームの時刻から±{ambiguous_sec}秒以内の非採用フレームを除外、上記件数は除外後)"
        )
    else:
        lines.append("曖昧ネガティブ除外: 無効（--ambiguous-sec が0以下）")
    lines.append("")
    lines.append("--- 特徴量セット比較 (GroupKFold, 動画単位のout-of-fold予測) ---")
    lines.append(f"{'features':>8} {'PR-AUC':>8} {'ROC-AUC':>8} {'件数':>6} {'動画数':>6}  備考")
    for row in comparison_rows:
        if row.get("skipped"):
            lines.append(
                f"{row['features']:>8} {'---':>8} {'---':>8} {row['n_samples']:>6} {row['n_videos']:>6}  "
                f"評価不可: {row.get('note', '')}"
            )
        else:
            lines.append(
                f"{row['features']:>8} {row['pr_auc']:>8.4f} {row['roc_auc']:>8.4f} "
                f"{row['n_samples']:>6} {row['n_videos']:>6}"
            )
    lines.append("")
    lines.append(f"最終モデルの特徴量セット(--features): {selected_features}")
    lines.append(f"ベースライン採用率: {baseline:.4f}")
    lines.append("")
    lines.append("しきい値ごとの precision / recall / 採用候補数 (最終モデル):")
    lines.append(f"{'閾値':>6} {'precision':>10} {'recall':>10} {'候補数':>8}")
    for row in threshold_rows:
        lines.append(
            f"{row['threshold']:>6.1f} {row['precision']:>10.4f} {row['recall']:>10.4f} "
            f"{int(row['predicted_positive']):>8d}"
        )
    lines.append("")
    lines.append("--- 特徴量重要度 (降順、最終モデル) ---")
    for name, importance in importances:
        lines.append(f"{name:>28}: {importance:.4f}")
    lines.append("")
    lines.append(f"モデル保存先: {model_out_path}")
    lines.append(f"メタ情報: {meta_path}")
    lines.append(f"レポート: {report_path}")
    return lines


def cmd_train(args: argparse.Namespace) -> int:
    root = resolve_root(args.root)
    dataset_path = resolve_path(root, args.dataset)
    model_out_path = resolve_path(root, args.model_out)
    features_mode: str = args.features
    ambiguous_sec: float = args.ambiguous_sec

    if not dataset_path.exists():
        print(f"エラー: 学習データが見つかりません: {dataset_path}")
        return 1

    with dataset_path.open("r", encoding="utf-8-sig", newline="") as file:
        rows = list(csv.DictReader(file))

    if not rows:
        print(f"エラー: 学習データが空です: {dataset_path}")
        return 1

    deduped = dedupe_dataset_rows(rows)
    deduped, ambiguous_excluded = exclude_ambiguous_negatives(deduped, ambiguous_sec)

    adopted = [row for row in deduped if (row.get("label") or "") == "1"]
    not_adopted = [row for row in deduped if (row.get("label") or "") != "1"]
    video_stems = sorted({row.get("video_stem", "") for row in deduped})

    if len(adopted) < 10:
        print(f"エラー: 採用フレーム(label=1)が{len(adopted)}件しかありません。学習には最低10件必要です。")
        return 1
    if len(adopted) < 30:
        print(
            f"警告: 採用フレーム(label=1)が{len(adopted)}件と少なめです(30件未満)。"
            "予測精度が不安定になる可能性がありますが、学習は続行します。"
        )
    if len(video_stems) < 2:
        print(f"エラー: 動画本数が{len(video_stems)}本しかありません。学習には最低2本必要です。")
        return 1

    medians = compute_medians(deduped)
    X_classic = np.array([build_feature_row(row, medians) for row in deduped], dtype=float)
    y = np.array([1 if (row.get("label") or "") == "1" else 0 for row in deduped], dtype=int)
    groups = np.array([row.get("video_stem", "") for row in deduped])
    classic_dim = X_classic.shape[1]

    # --- CLIP埋め込み(方式B)。比較表のため --features の値によらず常に計算します ---
    image_root = dataset_path.parent
    clip_model_path = root / CLIP_MODEL_RELATIVE_PATH
    clip_cache_dir = root / CLIP_CACHE_RELATIVE_DIR

    initial_indices, initial_paths = resolve_clip_targets(deduped, image_root)

    clip_error: str | None = None
    clip_valid_indices: list[int] = []
    X_clip_subset = np.zeros((0, CLIP_EMBED_DIM), dtype=float)

    if not initial_paths:
        clip_error = "dataset内でCLIP用画像を1件も解決できませんでした(image_pathが空、または画像ファイルが存在しません)"
    elif not clip_model_path.exists():
        clip_error = f"CLIPモデルが見つかりません: {clip_model_path}"
    else:
        embedder = ClipEmbedder(model_path=clip_model_path, cache_dir=clip_cache_dir)
        embedded_vectors = embedder.embed(initial_paths).astype(float)
        norms = np.linalg.norm(embedded_vectors, axis=1)
        keep_mask = norms >= 1e-6
        clip_valid_indices = [idx for idx, keep in zip(initial_indices, keep_mask) if keep]
        X_clip_subset = embedded_vectors[keep_mask]

    clip_missing_count = len(deduped) - len(clip_valid_indices)

    clip_indices_array = np.array(clip_valid_indices, dtype=int)
    y_clip_subset = y[clip_indices_array]
    groups_clip_subset = groups[clip_indices_array]
    X_classic_clip_subset = X_classic[clip_indices_array]
    X_both_subset = np.hstack([X_classic_clip_subset, X_clip_subset]) if X_clip_subset.shape[0] else np.zeros(
        (0, classic_dim + CLIP_EMBED_DIM)
    )

    # --- 特徴量セット3種類すべてを交差検証で比較 ---
    row_classic, oof_classic = evaluate_variant("classic", X_classic, y, groups, make_classic_model, None)

    clip_skip_reason = clip_error
    if clip_skip_reason is None and X_clip_subset.shape[0] == 0:
        clip_skip_reason = "CLIP埋め込みを1件も取得できませんでした"
    row_clip, oof_clip = evaluate_variant(
        "clip", X_clip_subset, y_clip_subset, groups_clip_subset, make_clip_model, clip_skip_reason
    )
    row_both, oof_both = evaluate_variant(
        "both",
        X_both_subset,
        y_clip_subset,
        groups_clip_subset,
        lambda: make_both_model(classic_dim),
        clip_skip_reason,
    )
    comparison_rows = [row_classic, row_clip, row_both]

    variants = {
        "classic": (row_classic, oof_classic, y),
        "clip": (row_clip, oof_clip, y_clip_subset),
        "both": (row_both, oof_both, y_clip_subset),
    }
    final_row, final_oof, final_y = variants[features_mode]
    if final_row.get("skipped") or final_oof is None:
        print(f"エラー: --features {features_mode} を評価できませんでした: {final_row.get('note', '')}")
        return 1

    pr_auc = float(final_row["pr_auc"])
    roc_auc = float(final_row["roc_auc"])
    baseline = float(final_y.mean())

    threshold_rows: list[dict[str, float]] = []
    for threshold in THRESHOLDS:
        predicted = (final_oof >= threshold).astype(int)
        precision = float(precision_score(final_y, predicted, zero_division=0))
        recall = float(recall_score(final_y, predicted, zero_division=0))
        threshold_rows.append(
            {
                "threshold": threshold,
                "precision": precision,
                "recall": recall,
                "predicted_positive": float(predicted.sum()),
            }
        )

    # 最終モデルは選択された --features で全データ(該当subset)を使い学習し直します。
    scaler: StandardScaler | None = None
    clip_model_rel_path: str | None = None
    if features_mode == "classic":
        model = make_classic_model()
        model.fit(X_classic, y)
    elif features_mode == "clip":
        model = make_clip_model()
        model.fit(X_clip_subset, y_clip_subset)
        clip_model_rel_path = CLIP_MODEL_RELATIVE_PATH.as_posix()
    else:
        model = make_both_model(classic_dim)
        model.fit(X_both_subset, y_clip_subset)
        scaler = model.named_steps["preprocess"].named_transformers_["classic_scaled"]
        clip_model_rel_path = CLIP_MODEL_RELATIVE_PATH.as_posix()

    importances = compute_importances(features_mode, model, classic_dim)

    model_out_path.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(
        {
            "model": model,
            "features": features_mode,
            "feature_names": FEATURE_NAMES,
            "medians": medians,
            "scaler": scaler,
            "clip_model_path": clip_model_rel_path,
            "clip_embed_dim": CLIP_EMBED_DIM if clip_model_rel_path else None,
        },
        model_out_path,
    )

    meta_path = model_out_path.parent / "train_meta.json"
    report_path = model_out_path.parent / "train_report.txt"

    counts = {
        "total": len(deduped),
        "adopted": len(adopted),
        "not_adopted": len(not_adopted),
        "videos": len(video_stems),
    }

    meta = {
        "trained_at": datetime.now().isoformat(),
        "counts": counts,
        "features": features_mode,
        "ambiguous_sec": ambiguous_sec,
        "ambiguous_excluded": ambiguous_excluded,
        "clip_missing_count": clip_missing_count,
        "feature_names": FEATURE_NAMES,
        "medians": medians,
        "comparison": comparison_rows,
        "cv_metrics": {
            "pr_auc": pr_auc,
            "roc_auc": roc_auc,
            "baseline_rate": baseline,
            "thresholds": threshold_rows,
        },
        "sklearn_version": sklearn.__version__,
    }
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")

    report_lines = build_train_report(
        counts,
        ambiguous_sec,
        ambiguous_excluded,
        comparison_rows,
        features_mode,
        baseline,
        threshold_rows,
        importances,
        model_out_path,
        meta_path,
        report_path,
    )
    report_path.write_text("\n".join(report_lines) + "\n", encoding="utf-8")

    print("\n".join(report_lines))
    print(f"CLIP画像未解決件数(image_path空/未存在/読込失敗): {clip_missing_count} 件")
    return 0


def cmd_predict(args: argparse.Namespace) -> int:
    root = resolve_root(args.root)
    model_path = resolve_path(root, args.model)
    out_path = resolve_path(root, args.out)

    if not model_path.exists():
        print(f"エラー: モデルファイルが見つかりません: {model_path}")
        print("先に train を実行してください。")
        return 1

    bundle = joblib.load(model_path)
    model = bundle["model"]
    medians = bundle["medians"]
    features_mode: str = bundle.get("features", "classic")
    clip_model_rel_path = bundle.get("clip_model_path")

    review_dir = root / "review" / "frames"
    result_csv_path = root / "logs" / "result.csv"
    work_audio_dir = root / "work" / "audio_peaks"

    frame_paths: list[Path] = []
    if review_dir.exists():
        frame_paths = sorted(
            path for path in review_dir.rglob("*") if path.is_file() and path.suffix.lower() in PHOTO_EXTENSIONS
        )

    parsed_frames: list[tuple[str, str, float]] = []
    name_mismatch_count = 0
    for path in frame_paths:
        match = FRAME_NAME_PATTERN.match(path.stem)
        if not match:
            print(f"警告: フレーム名の形式が不正なためスキップします: {path.name}")
            name_mismatch_count += 1
            continue
        parsed_frames.append((path.name, match.group("video_stem"), float(match.group("time"))))

    path_by_frame_name = {path.name: path for path in frame_paths}

    features_by_name = load_result_features(result_csv_path)
    audio_lookup = AudioPeakLookup(work_audio_dir)

    scored_rows: list[tuple[str, str, float, list[float]]] = []
    missing_result_count = 0
    for frame_name, video_stem, time_sec in parsed_frames:
        raw = features_by_name.get(frame_name)
        if raw is None:
            print(f"警告: logs/result.csv に情報が無いためスキップします: {frame_name}")
            missing_result_count += 1
            continue
        raw = dict(raw)
        raw["audio_peak"] = audio_lookup.get(video_stem, time_sec)
        vector = build_feature_row(raw, medians)
        scored_rows.append((frame_name, video_stem, time_sec, vector))

    if not scored_rows:
        print("エラー: スコア対象フレームが0件です。")
        print(
            "review/frames にフレーム画像があること、logs/result.csv にそのフレームの情報が"
            "登録されていることを確認してください。"
        )
        return 1

    X_classic = np.array([row[3] for row in scored_rows], dtype=float)

    clip_warning_count = 0
    clip_hit_count = 0
    clip_new_count = 0
    if features_mode == "classic":
        X = X_classic
    else:
        clip_model_path = (
            resolve_path(root, Path(clip_model_rel_path)) if clip_model_rel_path else root / CLIP_MODEL_RELATIVE_PATH
        )
        clip_cache_dir = root / CLIP_CACHE_RELATIVE_DIR
        if not clip_model_path.exists():
            print(f"エラー: CLIPモデルが見つかりません: {clip_model_path}")
            return 1
        embedder = ClipEmbedder(model_path=clip_model_path, cache_dir=clip_cache_dir)
        image_paths_for_scoring = [path_by_frame_name[row[0]] for row in scored_rows]
        X_clip = embedder.embed(image_paths_for_scoring).astype(float)
        clip_warning_count = len(embedder.warnings)
        clip_hit_count = embedder.cache_hit_count
        clip_new_count = embedder.cache_miss_count

        if features_mode == "clip":
            X = X_clip
        else:  # both
            X = np.hstack([X_classic, X_clip])

    proba = model.predict_proba(X)[:, 1]

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.writer(file)
        writer.writerow(["frame_name", "video_stem", "time_sec", "ai_score"])
        for (frame_name, video_stem, time_sec, _), score in zip(scored_rows, proba):
            writer.writerow([frame_name, video_stem, time_sec, f"{float(score):.4f}"])

    print("=== frame_scorer predict レポート ===")
    print(f"特徴量セット: {features_mode}")
    print(f"スコア対象: {len(scored_rows)} 件")
    print(f"スキップ: フレーム名不正 {name_mismatch_count} 件 / result.csv情報なし {missing_result_count} 件")
    if features_mode != "classic":
        print(
            f"CLIP埋め込み: キャッシュヒット {clip_hit_count} 件 / 新規推論 {clip_new_count} 件 / "
            f"警告(読込失敗) {clip_warning_count} 件"
        )
    print(f"スコア分布: 平均 {proba.mean():.4f} / 最大 {proba.max():.4f} / 最小 {proba.min():.4f}")
    print("--- スコア上位10件 ---")
    ranked = sorted(zip(scored_rows, proba), key=lambda item: item[1], reverse=True)[:10]
    for (frame_name, _video_stem, _time_sec, _vector), score in ranked:
        print(f"{frame_name}: {float(score):.4f}")
    print(f"出力先: {out_path}")
    return 0


def main() -> int:
    args = parse_args()
    if args.command == "train":
        return cmd_train(args)
    if args.command == "predict":
        return cmd_predict(args)
    print("エラー: 不明なサブコマンドです。", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
