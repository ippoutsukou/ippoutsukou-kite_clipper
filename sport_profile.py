"""スポーツプロファイルの読み込みモジュール。

`kite_clipper.py` を「カイトボード専用」から「スポーツプロファイル切り替え型」に拡張するための
基盤モジュールです。`clip_embedder.py` と同じ立ち位置の、他プロジェクトモジュールを import しない
末端モジュールとして実装しています（`frame_scorer.py` が `kite_clipper.py` / `record_labels.py` を
import しない設計方針に合わせています）。

未知の sport_id、`profiles/` フォルダの不在、JSON パースエラーなどはすべて推測でフォールバックせず、
`ProfileError` を送出して明示的に停止します。

詳細仕様: multi_sport/MULTI_SPORT_SPEC.md の 2.1 / 2.2 / 4.1
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

DEFAULT_SPORT_ID = "kite"

PROFILES_DIRNAME = "profiles"

# プロファイルJSONに必須のトップレベルキー
_REQUIRED_TOP_KEYS = (
    "id",
    "display_name",
    "equipment_detector",
    "equipment_detector_params",
    "cv",
    "segment",
    "paths",
)
_REQUIRED_PATH_KEYS = ("dataset", "model")

# 器材検出器としてサポートしている値（仕様書 2.2）
KNOWN_EQUIPMENT_DETECTORS = ("kite_hsv_upper", "none")


class ProfileError(Exception):
    """スポーツプロファイルの読み込み・解決に失敗したときの例外。

    推測でのフォールバックは行わず、常にこの例外で明示的に停止します。
    """


@dataclass(frozen=True)
class SportProfile:
    id: str
    display_name: str
    equipment_detector: str
    equipment_detector_params: dict
    cv: dict
    segment: dict
    dataset_path: Path  # root からの絶対パスに解決済み
    model_path: Path  # root からの絶対パスに解決済み


def _profiles_dir(root: Path) -> Path:
    return Path(root) / PROFILES_DIRNAME


def load_profile(root: Path, sport_id: str) -> SportProfile:
    """`<root>/profiles/<sport_id>.json` を読み込んで SportProfile を返す。

    `root` はアプリ本体の設置ディレクトリ（PyInstaller配布時は exe と同じ場所）を想定しており、
    `--root` で指定される作業フォルダとは別物です。dataset/model のパスもこの `root` を基準に解決します。
    """
    profiles_dir = _profiles_dir(root)
    if not profiles_dir.is_dir():
        raise ProfileError(
            f"プロファイルフォルダが見つかりません: {profiles_dir}\n"
            "profiles/ をリポジトリ直下（またはexeと同じ場所）に配置してください。"
        )

    profile_path = profiles_dir / f"{sport_id}.json"
    if not profile_path.is_file():
        raise ProfileError(
            f"未知のスポーツIDです: {sport_id!r}\n"
            f"プロファイルファイルが存在しません: {profile_path}"
        )

    try:
        raw_text = profile_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ProfileError(f"プロファイルを読み込めません: {profile_path} ({exc})") from exc

    try:
        data = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        raise ProfileError(f"プロファイルのJSONが不正です: {profile_path} ({exc})") from exc

    if not isinstance(data, dict):
        raise ProfileError(f"プロファイルの形式が不正です（JSONオブジェクトではありません）: {profile_path}")

    for key in _REQUIRED_TOP_KEYS:
        if key not in data:
            raise ProfileError(f"プロファイルに必須キー '{key}' がありません: {profile_path}")

    paths = data["paths"]
    if not isinstance(paths, dict):
        raise ProfileError(f"プロファイルの 'paths' がオブジェクトではありません: {profile_path}")
    for key in _REQUIRED_PATH_KEYS:
        if key not in paths:
            raise ProfileError(f"プロファイルの paths に必須キー '{key}' がありません: {profile_path}")

    if data["id"] != sport_id:
        raise ProfileError(
            f"プロファイルの id ({data['id']!r}) が要求された sport_id ({sport_id!r}) と"
            f" 一致しません: {profile_path}"
        )

    equipment_detector = data["equipment_detector"]
    if equipment_detector not in KNOWN_EQUIPMENT_DETECTORS:
        raise ProfileError(
            f"未知の equipment_detector です: {equipment_detector!r}"
            f"（対応値: {', '.join(KNOWN_EQUIPMENT_DETECTORS)}） プロファイル: {profile_path}"
        )

    return SportProfile(
        id=data["id"],
        display_name=data["display_name"],
        equipment_detector=equipment_detector,
        equipment_detector_params=data["equipment_detector_params"],
        cv=data["cv"],
        segment=data["segment"],
        dataset_path=(Path(root) / paths["dataset"]).resolve(),
        model_path=(Path(root) / paths["model"]).resolve(),
    )


def list_profiles(root: Path) -> list[SportProfile]:
    """`<root>/profiles/*.json` を走査し、SportProfile のリストを返す（表示名順ではなくファイル名順）。"""
    profiles_dir = _profiles_dir(root)
    if not profiles_dir.is_dir():
        raise ProfileError(
            f"プロファイルフォルダが見つかりません: {profiles_dir}\n"
            "profiles/ をリポジトリ直下（またはexeと同じ場所）に配置してください。"
        )
    return [load_profile(root, path.stem) for path in sorted(profiles_dir.glob("*.json"))]


def detect_equipment_candidate(image, profile: SportProfile, kite_detector) -> bool:
    """器材検出のディスパッチのみを行う（アルゴリズム本体は持たない）。

    `equipment_detector` が `"kite_hsv_upper"` のときは呼び出し元から渡された `kite_detector`
    （`kite_clipper.py` の `detect_kite_candidate()`）をそのまま呼ぶ。アルゴリズム本体を
    ここに移植しないのは、差分を最小化し既存の検出結果を1ビットも変えないため。
    `"none"` のときは常に False（CV検出そのものをスキップする）。
    """
    detector = profile.equipment_detector
    if detector == "kite_hsv_upper":
        return bool(kite_detector(image))
    if detector == "none":
        return False
    # load_profile() 側で検証済みのため通常はここに到達しないが、念のため明示エラーで停止する。
    raise ProfileError(f"未知の equipment_detector です: {detector!r}（プロファイル: {profile.id}）")
