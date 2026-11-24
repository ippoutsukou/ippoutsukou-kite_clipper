import 'clip_session.dart';

/// 一括保存の1件。区間番号は1始まり（画面の「区間N」と同じ）。
class BatchEntry {
  BatchEntry(
      {required this.item,
      required this.segment,
      required this.segmentNumber,
      required this.fileName});
  final ClipItem item;
  final ClipSegment segment;
  final int segmentNumber;
  final String fileName;
  int get lengthMs => segment.endMs - segment.startMs;
}

/// 一覧から保存対象を抽出する。ファイル操作は行わない。
class BatchPlan {
  BatchPlan._(this.entries, this.skippedSaved, this.unconfirmedVideos,
      this.noProposalVideos, this.wholeVideos, this.wholeMs);
  static const maxStemLength = 60;

  final List<BatchEntry> entries;

  /// 保存済みのためスキップした区間数（includeSaved が false のときのみ）。
  final int skippedSaved;

  /// 長さ未確認で区間がなく、対象外にした動画数。
  final int unconfirmedVideos;

  /// AIの提案が0個で区間に手を入れていないため、対象外にした動画数。
  final int noProposalVideos;

  /// 区間を決めていない（全体1区間のまま）で対象に入る動画の数と合計の長さ。
  /// 解析していない動画がそのまま長く入るのを、確認画面で気づけるようにする。
  final int wholeVideos;
  final int wholeMs;

  bool get isEmpty => entries.isEmpty;
  int get videoCount => entries.map((e) => e.item).toSet().length;
  int get totalMs => entries.fold(0, (sum, e) => sum + e.lengthMs);

  factory BatchPlan.from(List<ClipItem> items, {bool includeSaved = false}) {
    final entries = <BatchEntry>[];
    var skipped = 0;
    var unconfirmed = 0;
    var noProposals = 0;
    var whole = 0;
    var wholeMs = 0;
    for (var i = 0; i < items.length; i++) {
      final item = items[i];
      if (item.segments.isEmpty) {
        unconfirmed++;
        continue;
      }
      if (item.excludedNoProposals) {
        noProposals++;
        continue;
      }
      if (item.isUntouchedDefault) {
        whole++;
        wholeMs += item.durationMs!;
      }
      for (var n = 0; n < item.segments.length; n++) {
        final segment = item.segments[n];
        if (segment.output != null && !includeSaved) {
          skipped++;
          continue;
        }
        entries.add(BatchEntry(
            item: item,
            segment: segment,
            segmentNumber: n + 1,
            fileName: batchFileName(i + 1, item.name, n + 1)));
      }
    }
    return BatchPlan._(List.unmodifiable(entries), skipped, unconfirmed,
        noProposals, whole, wholeMs);
  }
}

/// `001_元動画名_区間2.mp4`。番号は一覧順（未確認の動画も番号を消費する）。
String batchFileName(int listNumber, String videoName, int segmentNumber) =>
    '${listNumber.toString().padLeft(3, '0')}_${safeStem(videoName)}_区間$segmentNumber.mp4';

/// 拡張子を除き、Windows/Androidでファイル名に使えない文字を `_` にして60文字に収める。
String safeStem(String name) {
  final dot = name.lastIndexOf('.');
  final stem = dot > 0 ? name.substring(0, dot) : name;
  final replaced = stem.replaceAll(RegExp(r'[<>:"/\\|?*\x00-\x1f]'), '_');
  final runes = replaced.runes.take(BatchPlan.maxStemLength);
  final result = String.fromCharCodes(runes).trim();
  return result.isEmpty ? 'video' : result;
}

/// 実行ごとの保存フォルダ名 `kite_clips_YYYYMMDD_HHMMSS`（端末の現地時刻）。
String batchFolderName(DateTime time) {
  String two(int value) => value.toString().padLeft(2, '0');
  return 'kite_clips_${time.year}${two(time.month)}${two(time.day)}_'
      '${two(time.hour)}${two(time.minute)}${two(time.second)}';
}

/// 既存と重なる場合は上書きせず `_2`, `_3`… を付ける。exists は保存先ごとに判定を差し替える。
String uniqueFileName(String name, bool Function(String) exists) {
  if (!exists(name)) return name;
  final dot = name.lastIndexOf('.');
  final stem = dot > 0 ? name.substring(0, dot) : name;
  final extension = dot > 0 ? name.substring(dot) : '';
  for (var n = 2;; n++) {
    final candidate = '${stem}_$n$extension';
    if (!exists(candidate)) return candidate;
  }
}
