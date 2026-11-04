import 'dart:convert';
import 'dart:io';
import 'dart:typed_data';

import 'package:flutter_test/flutter_test.dart';
import 'package:kite_clipper_flutter/clip_session.dart';
import 'package:kite_clipper_flutter/session_store.dart';

ClipSession sample() {
  final session = ClipSession();
  final items = session.add(['C:/v/A.mp4', 'C:/v/B.mp4', 'C:/v/C.mp4']);
  items[0].setDuration(20000);
  items[0].setRange(1000, 4000);
  items[0].output = 'saved-A1.mp4';
  items[0].addSegment(10000);
  items[0].setRange(10000, 12500);
  items[0].thumbnail = Uint8List(3);
  items[0].error = '表示だけのエラー';
  items[2].setDuration(8000);
  session.select(items[2]);
  return session;
}

List<List<Object?>> segments(ClipItem item) =>
    item.segments.map((s) => [s.startMs, s.endMs, s.output]).toList();

void main() {
  test('一覧・長さ・区間・選択・保存結果を往復し、サムネイルとエラーは記録しない', () {
    final restored = decodeSession(encodeSession(sample()), exists: (_) => true);
    final session = restored.session;
    expect(restored.skipped, 0);
    expect(restored.warning, isNull);
    expect(session.items.map((i) => i.path),
        ['C:/v/A.mp4', 'C:/v/B.mp4', 'C:/v/C.mp4']);
    expect(session.active?.path, 'C:/v/C.mp4');
    final a = session.items[0];
    expect(a.durationMs, 20000);
    expect(a.activeSegment, 1);
    expect(segments(a), [
      [1000, 4000, 'saved-A1.mp4'],
      [10000, 12500, null],
    ]);
    expect(a.thumbnail, isNull);
    expect(a.error, isNull);
    expect(session.items[1].durationMs, isNull);
    expect(session.items[1].segments, isEmpty);
    // 復元後もそのまま編集できる。
    a.addSegment(15000);
    expect(a.segments.length, 3);
    final text = encodeSession(sample(), now: DateTime.utc(2026, 9, 16));
    expect(text, contains('"savedAt": "2026-09-16T00:00:00.000Z"'));
  });

  test('不正な動画だけ読み飛ばし、件数を数える', () {
    final json = jsonEncode({
      'version': 1,
      'activePath': 'gone.mp4',
      'items': [
        {
          'path': 'ok.mp4',
          'durationMs': 5000,
          'activeSegment': 0,
          'segments': [
            {'startMs': 0, 'endMs': 5000, 'output': null}
          ]
        },
        {'path': 'ok.mp4', 'durationMs': null, 'activeSegment': 0, 'segments': []},
        {
          'path': 'over.mp4',
          'durationMs': 5000,
          'activeSegment': 0,
          'segments': [
            {'startMs': 0, 'endMs': 6000}
          ]
        },
        {
          'path': 'short.mp4',
          'durationMs': 5000,
          'activeSegment': 0,
          'segments': [
            {'startMs': 0, 'endMs': 50}
          ]
        },
        {
          'path': 'nodur.mp4',
          'durationMs': null,
          'activeSegment': 0,
          'segments': [
            {'startMs': 0, 'endMs': 1000}
          ]
        },
        {
          'path': 'badactive.mp4',
          'durationMs': 5000,
          'activeSegment': 3,
          'segments': [
            {'startMs': 0, 'endMs': 1000}
          ]
        },
        {'path': '', 'segments': []},
        'text',
        {'path': 'type.mp4', 'durationMs': '5000', 'activeSegment': 0, 'segments': []},
        {
          'path': 'many.mp4',
          'durationMs': 60000,
          'activeSegment': 0,
          'segments': [
            for (var i = 0; i < 21; i++) {'startMs': i * 1000, 'endMs': i * 1000 + 500}
          ]
        },
        {
          'path': 'badoutput.mp4',
          'durationMs': 5000,
          'activeSegment': 0,
          'segments': [
            {'startMs': 0, 'endMs': 1000, 'output': 5}
          ]
        },
        {'path': 'unconfirmed.mp4', 'durationMs': null, 'activeSegment': 0, 'segments': []},
      ]
    });
    final restored = decodeSession(json);
    expect(restored.session.items.map((i) => i.path), ['ok.mp4', 'unconfirmed.mp4']);
    expect(restored.skipped, 10);
    expect(restored.session.active, isNull);
  });

  test('120本の作業記録もすべて復元する', () {
    final json = jsonEncode({
      'version': 1,
      'items': [
        for (var i = 0; i < 120; i++)
          {'path': '$i.mp4', 'durationMs': null, 'activeSegment': 0, 'segments': []}
      ]
    });
    final restored = decodeSession(json);
    expect(restored.session.items.length, 120);
    expect(restored.skipped, 0);
  });

  test('全体の形式が違う記録は FormatException', () {
    expect(() => decodeSession('not json'), throwsFormatException);
    expect(() => decodeSession('{"version":2,"items":[]}'), throwsFormatException);
    expect(() => decodeSession('{"version":1}'), throwsFormatException);
    expect(() => decodeSession('[]'), throwsFormatException);
  });

  test('元動画が見つからない動画は区間を残してエラー表示にする', () {
    final restored = decodeSession(encodeSession(sample()),
        exists: (path) => path != 'C:/v/A.mp4');
    final a = restored.session.items[0];
    expect(a.error, missingSourceMessage);
    expect(a.segments.length, 2);
    expect(restored.session.items[2].error, isNull);
  });

  test('一時ファイル経由で保存し、直前の記録を .bak に残し、壊れた記録を退避して復元', () async {
    final base = await Directory('../out/flutter-proof').create(recursive: true);
    final run = await base.createTemp('session_');
    final store = SessionStore(Directory('${run.path}/nested'));

    final none = await store.load();
    expect(none.session.items, isEmpty);
    expect(none.warning, isNull);

    await store.save(sample());
    expect(store.file.existsSync(), isTrue);
    expect(store.tempFile.existsSync(), isFalse);
    expect(store.backupFile.existsSync(), isFalse);
    final first = await store.file.readAsString();

    final changed = sample();
    changed.remove(changed.items[1]);
    await store.save(changed);
    expect(await store.backupFile.readAsString(), first);
    expect(store.tempFile.existsSync(), isFalse);
    final loaded = await store.load();
    expect(loaded.session.items.length, 2);
    expect(loaded.warning, isNull);
    // テスト用のパスは実在しないため、見つからない扱いになる。
    expect(loaded.session.items.first.error, missingSourceMessage);

    await store.file.writeAsString('{壊れた記録');
    final recovered = await store.load();
    expect(recovered.session.items.length, 3);
    expect(recovered.warning, contains('1つ前の記録'));
    expect(store.file.existsSync(), isFalse);
    final corrupt = store.directory
        .listSync()
        .whereType<File>()
        .where((f) => f.path.contains('session.corrupt-'))
        .toList();
    expect(corrupt.length, 1);
    expect(await corrupt.single.readAsString(), '{壊れた記録');

    await store.backupFile.writeAsString('x');
    final empty = await store.load();
    expect(empty.session.items, isEmpty);
    expect(empty.warning, contains('新しく始めます'));
  });

  test('AI解析の結果も記録し、壊れていればその結果だけ捨てる', () {
    final session = sample();
    session.items[0].analysis = const AnalysisRecord(
        sportId: 'kite',
        intervalMs: 1000,
        durationMs: 20000,
        timesMs: [0, 1000],
        scores: [0.123456, 0.9]);
    final restored = decodeSession(encodeSession(session), exists: (_) => true);
    final analysis = restored.session.items[0].analysis!;
    expect(analysis.sportId, 'kite');
    expect(analysis.intervalMs, 1000);
    expect(analysis.timesMs, [0, 1000]);
    expect(analysis.scores, [0.123, 0.9]); // 小数3桁に丸めて保存する
    expect(analysis.partial, isFalse);
    expect(
        analysis.matches(sportId: 'kite', intervalMs: 1000, durationMs: 20000),
        isTrue);
    expect(
        analysis.matches(sportId: 'wing', intervalMs: 1000, durationMs: 20000),
        isFalse);
    expect(restored.session.items[2].analysis, isNull);

    // 中止した部分結果は使い回さない。
    const partial = AnalysisRecord(
        sportId: 'kite',
        intervalMs: 1000,
        durationMs: 20000,
        timesMs: [0],
        scores: [0.5],
        partial: true);
    expect(
        partial.matches(sportId: 'kite', intervalMs: 1000, durationMs: 20000),
        isFalse);

    // 件数が合わない記録は解析結果だけ捨て、動画と区間は残す。
    final broken = jsonDecode(encodeSession(session)) as Map<String, dynamic>;
    (broken['items'] as List)[0]['analysis']['scores'] = [0.1];
    final loaded = decodeSession(jsonEncode(broken));
    expect(loaded.session.items.length, 3);
    expect(loaded.session.items[0].analysis, isNull);
    expect(loaded.session.items[0].segments.length, 2);
    expect(loaded.skipped, 0);
  });

  test('ClipItem.restore は不正な区間を拒否し、元の状態を変えない', () {
    final item = ClipItem('a.mp4')..setDuration(9000);
    expect(
        () => item.restore(
            durationMs: 5000,
            segments: [ClipSegment(0, 6000)],
            activeSegment: 0),
        throwsArgumentError);
    expect(item.durationMs, 9000);
    expect(item.segments.single.endMs, 9000);
    item.restore(
        durationMs: 5000,
        segments: [ClipSegment(0, 1000), ClipSegment(2000, 3000)],
        activeSegment: 1);
    expect(item.startMs, 2000);
  });
}
