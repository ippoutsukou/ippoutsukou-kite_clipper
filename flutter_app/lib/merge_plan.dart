import 'dart:io';

import 'batch_plan.dart';
import 'clip_session.dart';

/// 1本にまとめるときの並び。動画一覧の順、同じ動画の中は区間番号順で、全区間が対象。
/// 保存済みの区間も含める（つなぐ対象は「保存したか」に左右されない）。
class MergePlan {
  MergePlan._(this.entries, this.missing, this.unconfirmedVideos,
      this.noProposalVideos, this.wholeVideos, this.wholeMs);
  final List<BatchEntry> entries;

  /// 元動画が見つからないため外した動画。
  final List<ClipItem> missing;

  /// 長さ未確認で区間がない動画の数。
  final int unconfirmedVideos;

  /// AIの提案が0個のため外した動画の数（[BatchPlan.noProposalVideos]）。
  final int noProposalVideos;

  /// 区間を決めていない（全体のまま）で入る動画の数と合計の長さ。
  final int wholeVideos;
  final int wholeMs;

  int get totalMs => entries.fold(0, (sum, e) => sum + e.lengthMs);
  int get videoCount => entries.map((e) => e.item).toSet().length;

  /// つなぐには2区間以上必要。
  bool get canMerge => entries.length >= 2;

  factory MergePlan.from(List<ClipItem> items,
      {bool Function(String path)? exists}) {
    final present = exists ?? (path) => File(path).existsSync();
    final missing = <ClipItem>[];
    final available = <ClipItem>[];
    for (final item in items) {
      if (item.segments.isNotEmpty && !present(item.path)) {
        missing.add(item);
        continue;
      }
      available.add(item);
    }
    final plan = BatchPlan.from(available, includeSaved: true);
    return MergePlan._(plan.entries, List.unmodifiable(missing),
        plan.unconfirmedVideos, plan.noProposalVideos, plan.wholeVideos,
        plan.wholeMs);
  }
}

/// まとめた動画のファイル名 `kite_merged_YYYYMMDD_HHMMSS.mp4`（端末の現地時刻）。
/// 形式ごとに分けるときは [format]（例 `1920x1080_30fps`）を後ろに付ける。
String mergedFileName(DateTime time, {String? format}) {
  String two(int value) => value.toString().padLeft(2, '0');
  return 'kite_merged_${time.year}${two(time.month)}${two(time.day)}_'
      '${two(time.hour)}${two(time.minute)}${two(time.second)}'
      '${format == null ? '' : '_$format'}.mp4';
}
