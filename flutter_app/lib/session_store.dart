import 'dart:convert';
import 'dart:io';

import 'clip_session.dart';
import 'export_service.dart';
import 'omakase.dart';

const sessionFormatVersion = 1;
const missingSourceMessage =
    '元動画が見つかりません。移動・削除された可能性があります。一覧から外すか、動画を追加し直してください。';

class RestoredSession {
  const RestoredSession(this.session,
      {this.skipped = 0, this.warning, this.omakase = const OmakaseSettings()});
  final ClipSession session;

  /// おまかせモードの設定。記録がない・不正な場合はOFF。
  final OmakaseSettings omakase;

  /// 記録が不正で読み飛ばした動画の本数。
  final int skipped;

  /// 壊れた記録から立ち直った場合などの案内。
  final String? warning;
}

/// サムネイル・エラー・再生位置は記録しない（開いたときに作り直す）。
String encodeSession(ClipSession session,
        {DateTime? now, OmakaseSettings omakase = const OmakaseSettings()}) =>
    const JsonEncoder.withIndent('  ').convert({
      'version': sessionFormatVersion,
      'savedAt': (now ?? DateTime.now()).toUtc().toIso8601String(),
      'activePath': session.active?.path,
      'omakase': omakase.toJson(),
      'items': [
        for (final item in session.items)
          {
            'path': item.path,
            'durationMs': item.durationMs,
            'activeSegment': item.activeSegment,
            'analysis': item.analysis?.toJson(),
            'segments': [
              for (final segment in item.segments)
                {
                  'startMs': segment.startMs,
                  'endMs': segment.endMs,
                  'output': segment.output,
                }
            ],
          }
      ],
    });

/// 全体の形式が違う場合だけ FormatException。動画1本分の不正はその動画だけ読み飛ばす。
RestoredSession decodeSession(String text,
    {bool caseInsensitivePaths = false, bool Function(String path)? exists}) {
  final data = jsonDecode(text);
  if (data is! Map ||
      data['version'] != sessionFormatVersion ||
      data['items'] is! List) {
    throw const FormatException('作業記録の形式が違います');
  }
  final session = ClipSession(caseInsensitivePaths: caseInsensitivePaths);
  var skipped = 0;
  for (final raw in data['items'] as List) {
    ClipItem? item;
    // 型違い（TypeError）や範囲外（ArgumentError）も含め、どの理由でもその動画だけ捨てる。
    try {
      if (raw is! Map) throw const FormatException('動画の記録が不正です');
      final path = raw['path'];
      if (path is! String || path.isEmpty) {
        throw const FormatException('動画のパスがありません');
      }
      final added = session.add([path]);
      if (added.isEmpty) throw const FormatException('重複した動画です');
      item = added.single;
      final segments = raw['segments'];
      if (segments is! List) throw const FormatException('区間の記録が不正です');
      final active = raw['activeSegment'];
      item.restore(
          durationMs: _intOrNull(raw['durationMs']),
          segments: [for (final s in segments) _segment(s)],
          activeSegment: active is int ? active : 0);
      if (exists != null && !exists(path)) item.error = missingSourceMessage;
      // 解析結果が壊れている場合は、その結果だけ捨てて動画と区間は残す。
      final analysis = raw['analysis'];
      if (analysis != null) {
        try {
          item.analysis = AnalysisRecord.fromJson(analysis);
        } on FormatException {
          item.analysis = null;
        }
      }
    } catch (_) {
      if (item != null) session.remove(item);
      skipped++;
    }
  }
  final activePath = data['activePath'];
  for (final item in session.items) {
    if (item.path == activePath) {
      session.select(item);
      break;
    }
  }
  return RestoredSession(session,
      skipped: skipped, omakase: OmakaseSettings.fromJson(data['omakase']));
}

int? _intOrNull(Object? value) => switch (value) {
      null => null,
      int v => v,
      _ => throw const FormatException('数値ではありません'),
    };

ClipSegment _segment(Object? raw) {
  if (raw is! Map) throw const FormatException('区間の記録が不正です');
  final start = raw['startMs'], end = raw['endMs'], output = raw['output'];
  if (start is! int || end is! int || (output != null && output is! String)) {
    throw const FormatException('区間の記録が不正です');
  }
  return ClipSegment(start, end)..output = output as String?;
}

/// アプリ専用の場所に作業記録を1つ持つ。
class SessionStore {
  SessionStore(this.directory);
  final Directory directory;

  String _path(String name) =>
      '${directory.path}${Platform.pathSeparator}$name';
  File get file => File(_path('session.json'));
  File get backupFile => File(_path('session.json.bak'));
  File get tempFile => File(_path('session.json.tmp'));

  /// Windows は %APPDATA%\kite_clipper、Android はアプリ専用領域。決められなければ null（記録しない）。
  static Future<SessionStore?> open() async {
    if (Platform.isAndroid) {
      final dir =
          await ExportService.channel.invokeMethod<String>('appDataDir');
      return dir == null || dir.isEmpty ? null : SessionStore(Directory(dir));
    }
    if (Platform.isWindows) {
      final appData = Platform.environment['APPDATA'];
      if (appData == null || appData.isEmpty) return null;
      return SessionStore(Directory('$appData\\kite_clipper'));
    }
    return null;
  }

  /// 書きかけで壊さないよう一時ファイルに書いてから置き換え、直前の記録を .bak に残す。
  Future<void> save(ClipSession session,
      {OmakaseSettings omakase = const OmakaseSettings()}) async {
    final text = encodeSession(session, omakase: omakase); // 待機前に内容を確定する
    await directory.create(recursive: true);
    await tempFile.writeAsString(text, flush: true);
    if (await file.exists()) await file.copy(backupFile.path);
    await tempFile.rename(file.path);
  }

  Future<RestoredSession> load({bool caseInsensitivePaths = false}) async {
    String? warning;
    final main = file;
    for (final candidate in [main, backupFile]) {
      if (!await candidate.exists()) continue;
      try {
        final restored = decodeSession(await candidate.readAsString(),
            caseInsensitivePaths: caseInsensitivePaths,
            exists: (path) => File(path).existsSync());
        if (candidate.path == main.path) return restored;
        return RestoredSession(restored.session,
            skipped: restored.skipped,
            omakase: restored.omakase,
            warning: '前回の作業記録が壊れていたため、1つ前の記録から復元しました。');
      } on FormatException {
        if (candidate.path == main.path) {
          // 原因を調べられるよう削除せず別名で残す。
          await main.rename(_path(
              'session.corrupt-${DateTime.now().millisecondsSinceEpoch}.json'));
        }
        warning =
            '前回の作業記録を読み込めなかったため、新しく始めます。壊れた記録は ${directory.path} に残しています。';
      }
    }
    return RestoredSession(
        ClipSession(caseInsensitivePaths: caseInsensitivePaths),
        warning: warning);
  }
}
