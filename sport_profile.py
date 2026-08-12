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

import csv
import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

DEFAULT_SPORT_ID = "kite"

PROFILES_DIRNAME = "profiles"

# 混在ガード（仕様書 2.4）の対象。input/videos・input/photos は元データのため対象外。
ACTIVE_PROFILE_RELATIVE_PATH = "logs/active_profile.json"
_GUARD_TARGET_RELATIVE_DIRS = ("review/frames", "selected_frames")
_GUARD_TARGET_RELATIVE_CSV_FILES = ("logs/ai_scores.csv", "logs/ai_segments.csv")

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


class ProfileSwitchGuardError(Exception):
    """プロファイル混在ガード（仕様書 2.4）に引っかかったときの例外。

    作業フォルダ（`--root`）に前回の競技の中間生成物が残ったまま別競技を実行しようとした
    場合に送出する。呼び出し側は `str(exc)` をそのまま表示し、終了コード2で停止すること。
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
    """`<root>/profiles/*.json` を走査し、SportProfile のリストを返す（表示名順ではなくファイル名順）。

    1件でも読み込みに失敗すると `ProfileError` を送出して全体を止める。CLI 用の厳格な挙動であり、
    GUI のドロップダウン生成にはこの関数をそのまま使わない（3-0）。GUI 側は `list_profiles_lenient()` を使う。
    """
    profiles_dir = _profiles_dir(root)
    if not profiles_dir.is_dir():
        raise ProfileError(
            f"プロファイルフォルダが見つかりません: {profiles_dir}\n"
            "profiles/ をリポジトリ直下（またはexeと同じ場所）に配置してください。"
        )
    return [load_profile(root, path.stem) for path in sorted(profiles_dir.glob("*.json"))]


def list_profiles_lenient(root: Path) -> tuple[list[SportProfile], list[tuple[str, str]]]:
    """GUI用の緩い一覧取得。読めたプロファイルだけを返し、読めなかったものはエラーとして分けて返す。

    `list_profiles()` と異なり例外を送出しない（1ファイルの不備でGUIが起動不能になるのを避けるため。
    仕様書 TASKS.md Phase3 3-0 に対応）。

    戻り値: (読み込めた SportProfile のリスト, [(ファイル名, エラー理由), ...])
    `profiles/` フォルダ自体が無い場合は、空のプロファイル一覧と
    フォルダ不在を1件のエラーとして返す（例外は送出しない）。
    """
    profiles_dir = _profiles_dir(root)
    if not profiles_dir.is_dir():
        return [], [(PROFILES_DIRNAME, f"プロファイルフォルダが見つかりません: {profiles_dir}")]

    profiles: list[SportProfile] = []
    errors: list[tuple[str, str]] = []
    for path in sorted(profiles_dir.glob("*.json")):
        try:
            profiles.append(load_profile(root, path.stem))
        except ProfileError as exc:
            errors.append((path.name, str(exc)))
    return profiles, errors


def find_residual_artifacts(root: Path) -> list[str]:
    """混在ガード対象のフォルダ・CSVに中身が残っているかを、`active_profile.json` の内容とは
    無関係に確認する（GUIの競技切り替え確認ダイアログ用。仕様書 2.5 / TASKS.md 3-1）。

    `check_profile_switch()` は「前回の sport_id と食い違う場合」に限定して判定するが、
    GUIの切り替え確認ダイアログは「そもそも中間生成物が残っているか」だけを見たいため、
    判定ロジック（対象フォルダ・CSV）を共有しつつ、こちらは無条件に走査する。
    """
    findings: list[str] = []
    for relative in _GUARD_TARGET_RELATIVE_DIRS:
        count = _count_files_recursive(root / relative)
        if count > 0:
            findings.append(f"{relative} に {count} 件の未処理データが残っています。")
    for relative in _GUARD_TARGET_RELATIVE_CSV_FILES:
        count = _count_csv_data_rows(root / relative)
        if count > 0:
            findings.append(f"{relative} に {count} 件の未処理データが残っています。")
    return findings


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


def _count_files_recursive(directory: Path) -> int:
    if not directory.is_dir():
        return 0
    return sum(1 for path in directory.rglob("*") if path.is_file())


def _count_csv_data_rows(csv_path: Path) -> int:
    """CSVのデータ行数（ヘッダ除く）を返す。読めない・空の場合は0。"""
    if not csv_path.is_file() or csv_path.stat().st_size == 0:
        return 0
    try:
        with csv_path.open("r", encoding="utf-8-sig", newline="") as file:
            row_count = sum(1 for _ in csv.reader(file))
    except OSError:
        return 0
    return max(0, row_count - 1)


def check_profile_switch(root: Path, sport_id: str, allow_switch: bool) -> None:
    """作業フォルダ `root` に残る前回競技の中間生成物と `sport_id` の食い違いを検知する。

    詳細仕様: multi_sport/MULTI_SPORT_SPEC.md の 2.4。
    `root / logs/active_profile.json` に記録された前回の sport_id が今回の sport_id と異なり、
    かつ review/frames・selected_frames・logs/ai_scores.csv・logs/ai_segments.csv の
    いずれかに中身が残っている場合、`ProfileSwitchGuardError` を送出する。
    `allow_switch` が True のときは常に何もしない（`--allow-profile-switch` によるバイパス）。
    `input/videos` / `input/photos` は元データのためガード対象に含めない。
    active_profile.json が存在しない、または壊れている場合はガード判定を行わない
    （初回実行やこの機能導入前のワークフォルダを誤ってブロックしないため）。
    """
    if allow_switch:
        return

    active_path = root / ACTIVE_PROFILE_RELATIVE_PATH
    if not active_path.is_file():
        return

    try:
        raw_text = active_path.read_text(encoding="utf-8")
        data = json.loads(raw_text)
    except (OSError, json.JSONDecodeError):
        return
    if not isinstance(data, dict):
        return

    previous_sport_id = data.get("sport_id")
    if not previous_sport_id or previous_sport_id == sport_id:
        return

    findings = find_residual_artifacts(root)
    if not findings:
        return

    message_lines = [
        f"前回は {previous_sport_id} で実行しましたが、今回は {sport_id} が指定されています。",
        *findings,
        "競技を切り替える前に、以下のいずれかを行ってください。",
        "  - python kite_clipper.py --cleanup-review --confirm-delete で整理する",
        "  - --allow-profile-switch を付けて、混在を承知のうえで続行する",
    ]
    raise ProfileSwitchGuardError("\n".join(message_lines))


def write_active_profile(root: Path, sport_id: str) -> None:
    """実行成功時に `logs/active_profile.json` へ今回の sport_id を書き出す（混在ガード用）。"""
    logs_dir = root / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    payload = {"sport_id": sport_id, "updated_at": datetime.now().isoformat()}
    active_path = logs_dir / "active_profile.json"
    active_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
