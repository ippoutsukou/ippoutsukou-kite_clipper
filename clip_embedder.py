from __future__ import annotations

import argparse
import time
from pathlib import Path

import cv2
import numpy as np
import onnxruntime as ort

try:
    cv2.setLogLevel(0)
except AttributeError:
    pass

# record_labels.py / frame_scorer.py と同じ拡張子集合（CLI動作確認用）。
PHOTO_EXTENSIONS = {".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff", ".webp"}

# vendor/clip/SOURCE.txt に記載された前処理仕様（CLIP ViT-B/32 画像エンコーダー用）。
IMAGE_SIZE = 224
CLIP_EMBED_DIM = 512
CLIP_MEAN = np.array([0.48145466, 0.4578275, 0.40821073], dtype=np.float32)
CLIP_STD = np.array([0.26862954, 0.26130258, 0.27577711], dtype=np.float32)
DEFAULT_BATCH_SIZE = 16


def l2_normalize(vector: np.ndarray) -> np.ndarray:
    """1件分のベクトルをL2正規化します。ゼロベクトルはそのまま返します。"""
    norm = float(np.linalg.norm(vector))
    if norm < 1e-12:
        return vector.astype(np.float32)
    return (vector / norm).astype(np.float32)


class ClipEmbedder:
    """CLIP画像エンコーダー(ONNX量子化版)で画像をベクトル化します。

    onnxruntime.InferenceSession は初回の embed() 呼び出し時に遅延生成します。
    画像ごとにディスクキャッシュ(.npy)を持ち、再計算を避けます。
    """

    def __init__(self, model_path: Path, cache_dir: Path) -> None:
        self.model_path = model_path
        self.cache_dir = cache_dir
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._session: ort.InferenceSession | None = None

        # embed() 呼び出しのたびにリセットされる、直近実行分の統計情報。
        self.warnings: list[str] = []
        self.cache_hit_count = 0
        self.cache_miss_count = 0

    @property
    def session(self) -> ort.InferenceSession:
        if self._session is None:
            if not self.model_path.exists():
                raise FileNotFoundError(f"CLIPモデルが見つかりません: {self.model_path}")
            self._session = ort.InferenceSession(str(self.model_path), providers=["CPUExecutionProvider"])
        return self._session

    def _cache_path(self, image_path: Path) -> Path:
        # 日本語パスのファイル名もそのまま使えるよう、親フォルダ名+ファイル名+mtimeでキーを作ります。
        parent_name = image_path.resolve().parent.name
        try:
            mtime = int(image_path.stat().st_mtime)
        except OSError:
            mtime = 0
        return self.cache_dir / f"{parent_name}__{image_path.name}__{mtime}.npy"

    @staticmethod
    def _read_image(path: Path) -> np.ndarray | None:
        # 日本語などのパスでも読めるように np.fromfile 経由でデコードします（record_labels.py と同じ方式）。
        try:
            data = np.fromfile(str(path), dtype=np.uint8)
        except OSError:
            return None
        if data.size == 0:
            return None
        try:
            image = cv2.imdecode(data, cv2.IMREAD_COLOR)
        except cv2.error:
            return None
        return image

    @staticmethod
    def _resize_short_side(image_rgb: np.ndarray, target: int = IMAGE_SIZE) -> np.ndarray:
        height, width = image_rgb.shape[:2]
        if height <= width:
            new_height = target
            new_width = max(target, int(round(width * target / height)))
        else:
            new_width = target
            new_height = max(target, int(round(height * target / width)))
        return cv2.resize(image_rgb, (new_width, new_height), interpolation=cv2.INTER_AREA)

    @staticmethod
    def _center_crop(image: np.ndarray, size: int = IMAGE_SIZE) -> np.ndarray:
        height, width = image.shape[:2]
        top = max(0, (height - size) // 2)
        left = max(0, (width - size) // 2)
        return image[top : top + size, left : left + size]

    def _preprocess(self, image_bgr: np.ndarray) -> np.ndarray:
        """BGR画像 -> CHW float32 (3,224,224) の正規化済みテンソルに変換します。"""
        image_rgb = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2RGB)
        resized = self._resize_short_side(image_rgb)
        cropped = self._center_crop(resized)
        if cropped.shape[:2] != (IMAGE_SIZE, IMAGE_SIZE):
            raise ValueError(f"中央クロップ後のサイズが不正です: {cropped.shape}")
        normalized = cropped.astype(np.float32) / 255.0
        normalized = (normalized - CLIP_MEAN) / CLIP_STD
        chw = normalized.transpose(2, 0, 1)
        return np.ascontiguousarray(chw, dtype=np.float32)

    def embed(self, image_paths: list[Path], batch_size: int = DEFAULT_BATCH_SIZE) -> np.ndarray:
        """画像パスのリストを (N, 512) float32 のL2正規化済みベクトルに変換します。

        読み込めない画像はゼロベクトルとし、self.warnings に警告文を追記します（例外にしません）。
        """
        self.warnings = []
        self.cache_hit_count = 0
        self.cache_miss_count = 0

        n = len(image_paths)
        result = np.zeros((n, CLIP_EMBED_DIM), dtype=np.float32)
        if n == 0:
            return result

        cache_paths = [self._cache_path(path) for path in image_paths]

        pending_indices: list[int] = []
        pending_tensors: list[np.ndarray] = []

        for index, (path, cache_path) in enumerate(zip(image_paths, cache_paths)):
            if cache_path.exists():
                try:
                    cached_vector = np.load(cache_path)
                    if cached_vector.shape == (CLIP_EMBED_DIM,):
                        result[index] = cached_vector
                        self.cache_hit_count += 1
                        continue
                except Exception:
                    pass  # キャッシュが壊れている場合は再計算します。

            image = self._read_image(path)
            if image is None:
                warning = f"警告: 画像を読み込めません: {path}"
                self.warnings.append(warning)
                print(warning)
                continue  # ゼロベクトルのまま

            try:
                tensor = self._preprocess(image)
            except Exception as exc:
                warning = f"警告: 前処理に失敗しました: {path} ({exc})"
                self.warnings.append(warning)
                print(warning)
                continue  # ゼロベクトルのまま

            pending_indices.append(index)
            pending_tensors.append(tensor)

        if pending_tensors:
            input_name = self.session.get_inputs()[0].name
            output_name = self.session.get_outputs()[0].name
            for start in range(0, len(pending_tensors), batch_size):
                batch_indices = pending_indices[start : start + batch_size]
                batch_tensors = pending_tensors[start : start + batch_size]
                batch_input = np.stack(batch_tensors, axis=0).astype(np.float32)
                outputs = self.session.run([output_name], {input_name: batch_input})[0]
                self.cache_miss_count += len(batch_indices)
                for index, raw_vector in zip(batch_indices, outputs):
                    normalized_vector = l2_normalize(np.asarray(raw_vector, dtype=np.float32))
                    result[index] = normalized_vector
                    try:
                        np.save(cache_paths[index], normalized_vector)
                    except OSError as exc:
                        print(f"警告: キャッシュ保存に失敗しました: {cache_paths[index]} ({exc})")

        return result


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="画像フォルダ内の画像をCLIPで埋め込みベクトル化します（動作確認用CLI）。"
    )
    parser.add_argument("image_dir", type=Path, help="埋め込み対象の画像フォルダ（再帰的に探索します）")
    parser.add_argument(
        "--cache-dir",
        type=Path,
        default=Path("work/embeddings"),
        help="埋め込みベクトルのキャッシュ保存先（既定: work/embeddings）",
    )
    parser.add_argument(
        "--model",
        type=Path,
        default=Path("vendor/clip/vision_model_quantized.onnx"),
        help="CLIP visionモデル(ONNX)のパス（既定: vendor/clip/vision_model_quantized.onnx）",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    if not args.image_dir.exists():
        print(f"エラー: 画像フォルダが見つかりません: {args.image_dir}")
        return 1

    image_paths = sorted(
        path for path in args.image_dir.rglob("*") if path.is_file() and path.suffix.lower() in PHOTO_EXTENSIONS
    )
    if not image_paths:
        print(f"エラー: 画像が見つかりません: {args.image_dir}")
        return 1

    if not args.model.exists():
        print(f"エラー: CLIPモデルが見つかりません: {args.model}")
        return 1

    embedder = ClipEmbedder(model_path=args.model, cache_dir=args.cache_dir)

    start = time.perf_counter()
    vectors = embedder.embed(image_paths)
    elapsed = time.perf_counter() - start

    norms = np.linalg.norm(vectors, axis=1)

    print("=== clip_embedder 動作確認 ===")
    print(f"画像件数: {len(image_paths)}")
    print(f"埋め込み次元: {vectors.shape[1]}")
    print(f"処理時間: {elapsed:.2f}秒")
    print(f"キャッシュヒット: {embedder.cache_hit_count} 件 / 新規推論: {embedder.cache_miss_count} 件")
    print(f"L2ノルム: 平均 {norms.mean():.4f} / 最小 {norms.min():.4f} / 最大 {norms.max():.4f}")
    if embedder.warnings:
        print(f"警告件数: {len(embedder.warnings)} 件")
    print(f"キャッシュ保存先: {args.cache_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
