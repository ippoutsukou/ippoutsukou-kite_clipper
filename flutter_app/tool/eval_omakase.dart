// 評価専用の起動ファイル（アプリ本体には含まれない）。
//
// フォルダ内の動画すべてに、おまかせと同じ条件（1秒間隔・resolveIntervalMs）で AI解析をかけ、
// 4モデル分の点数と、おまかせが入れる提案区間（buildProposedSegments）を JSON に書き出して終了する。
// 解析は AnalysisService（FFmpeg＋Windows側のCLIP）をそのまま使うので、アプリと同じ結果になる。
//
// 出力先は Documents 配下にしないこと（Defenderのフォルダーアクセス制御で、Debug版exeの書き込みが拒否される）。
//
// 実行例（flutter_app で）：
//   flutter run -d windows -t tool/eval_omakase.dart \
//     --dart-define=EVAL_DIR=<動画フォルダ> --dart-define=EVAL_OUT=<出力JSON>
import 'dart:convert';
import 'dart:io';

import 'package:flutter/material.dart';
import 'package:kite_clipper_flutter/ai_segments.dart';
import 'package:kite_clipper_flutter/analysis_service.dart';
import 'package:kite_clipper_flutter/clip_session.dart';
import 'package:kite_clipper_flutter/omakase.dart';

const _dir = String.fromEnvironment('EVAL_DIR');
const _out = String.fromEnvironment('EVAL_OUT');

Future<void> main() async {
  WidgetsFlutterBinding.ensureInitialized();
  final status = ValueNotifier<String>('準備中');
  runApp(MaterialApp(
      home: Scaffold(
          body: Center(
              child: ValueListenableBuilder<String>(
                  valueListenable: status, builder: (_, v, __) => Text(v))))));
  try {
    exitCode = await _run(status);
  } catch (e, st) {
    stderr.writeln('EVAL 失敗: $e\n$st');
    exitCode = 1;
  }
  exit(exitCode);
}

Future<int> _run(ValueNotifier<String> status) async {
  if (_dir.isEmpty || _out.isEmpty) {
    stderr.writeln('EVAL_DIR と EVAL_OUT を --dart-define で指定してください');
    return 2;
  }
  final out = File(_out);
  if (await out.exists()) {
    stderr.writeln('出力先が既にあります（上書きしません）: $_out');
    return 2;
  }
  const settings = OmakaseSettings();
  final models = [for (final id in sportModelIds) await loadSportModel(id)];
  final videos = Directory(_dir)
      .listSync()
      .whereType<File>()
      .where((f) => RegExp(r'\.(mp4|mov|m4v)$', caseSensitive: false).hasMatch(f.path))
      .toList()
    ..sort((a, b) => a.path.compareTo(b.path));
  final service = AnalysisService();
  final items = <Map<String, Object?>>[];
  await out.parent.create(recursive: true);
  // 途中で止まっても結果が残るよう、1本ごとに全体を書き直す。
  Future<void> save() => out.writeAsString(const JsonEncoder.withIndent(' ').convert({
        'createdAt': DateTime.now().toUtc().toIso8601String(),
        'intervalMs': settings.intervalMs,
        'maxSegments': ClipItem.maxSegments,
        'models': {
          for (final m in models)
            m.id: {
              'enter': m.enter,
              'exit': m.exit,
              'smoothWindow': m.smoothWindow,
              'preRollMs': m.preRollMs,
              'postRollMs': m.postRollMs,
            }
        },
        'items': items,
      }));
  final watch = Stopwatch()..start();
  for (var i = 0; i < videos.length; i++) {
    final path = videos[i].absolute.path;
    final name = path.split(Platform.pathSeparator).last;
    status.value = '${i + 1}/${videos.length} $name';
    stdout.writeln('EVAL ${i + 1}/${videos.length} $name');
    try {
      // おまかせでは動画を開く前に解析するため、長さは未確定（0）として間隔を決める。
      final interval = resolveIntervalMs(durationMs: 0, wantedMs: settings.intervalMs);
      final result = await service.analyze(
          source: path, model: models.first, others: models.skip(1).toList(), intervalMs: interval);
      items.add({
        'name': name,
        'durationMs': result.durationMs,
        'intervalMs': interval,
        'partial': result.cancelled || result.truncated,
        'timesMs': result.timesMs,
        'totalMs': result.totalMs,
        'models': {
          for (final m in models)
            m.id: {
              'scores': [
                for (final s in result.scoresByModel[m.id]!) double.parse(s.toStringAsFixed(4))
              ],
              // applyOmakaseResult と同じ作り方。上限（maxSegments）超えも記録する。
              'segments': [
                for (final p in buildProposedSegments(
                    timesMs: result.timesMs,
                    scores: result.scoresByModel[m.id]!,
                    model: m,
                    durationMs: result.durationMs))
                  {'startMs': p.startMs, 'endMs': p.endMs, 'meanScore': p.meanScore}
              ],
            }
        },
      });
    } catch (e) {
      stderr.writeln('EVAL 失敗 $name: $e');
      items.add({'name': name, 'error': '$e'});
    }
    await save();
  }
  stdout.writeln('EVAL 完了 ${videos.length}本 ${watch.elapsed.inSeconds}秒 → $_out');
  return 0;
}
