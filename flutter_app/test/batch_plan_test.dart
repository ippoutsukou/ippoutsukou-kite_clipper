import 'package:flutter_test/flutter_test.dart';
import 'package:kite_clipper_flutter/batch_plan.dart';
import 'package:kite_clipper_flutter/clip_session.dart';

void main() {
  List<ClipItem> sample() {
    final session = ClipSession();
    final items = session.add(['C:/v/A.mp4', 'C:/v/B.mov', 'C:/v/C.MOV']);
    items[0].setDuration(20000);
    items[0].setRange(0, 3000);
    items[0].addSegment(10000);
    items[0].output = 'saved.mp4'; // 区間2を保存済みにする
    items[2].setDuration(8000); // 区間1：全体8秒
    return items; // Bは未確認
  }

  test('未確認の動画を除外し、保存済みの区間は初期値でスキップ', () {
    final plan = BatchPlan.from(sample());
    expect(plan.entries.map((e) => e.fileName),
        ['001_A_区間1.mp4', '003_C_区間1.mp4']);
    expect(plan.entries.map((e) => e.segmentNumber), [1, 1]);
    expect(plan.skippedSaved, 1);
    expect(plan.unconfirmedVideos, 1);
    expect(plan.videoCount, 2);
    expect(plan.totalMs, 3000 + 8000);
    expect(plan.isEmpty, isFalse);
  });

  test('保存し直す指定では保存済みも一覧順・区間順で含める', () {
    final items = sample();
    final plan = BatchPlan.from(items, includeSaved: true);
    expect(plan.entries.map((e) => e.fileName),
        ['001_A_区間1.mp4', '001_A_区間2.mp4', '003_C_区間1.mp4']);
    expect(plan.entries[1].segment, same(items[0].segments[1]));
    expect(plan.skippedSaved, 0);
    expect(plan.totalMs, 3000 + 5000 + 8000);
    expect(() => plan.entries.clear(), throwsUnsupportedError);
  });

  test('対象が0件の計画は空', () {
    final session = ClipSession()..add(['a.mp4', 'b.mp4']);
    final plan = BatchPlan.from(session.items);
    expect(plan.isEmpty, isTrue);
    expect(plan.unconfirmedVideos, 2);
    expect(plan.videoCount, 0);
    expect(plan.totalMs, 0);
  });

  test('ファイル名に使えない文字・制御文字を置換し拡張子を除く', () {
    expect(safeStem('a<b>:c"d|e?f*g.part.MOV'), 'a_b__c_d_e_f_g.part');
    expect(safeStem('tab\tname\u0001.mp4'), 'tab_name_');
    expect(safeStem('  .mp4'), 'video');
    expect(safeStem(''), 'video');
    expect(safeStem('.hidden'), '.hidden');
    expect(batchFileName(12, 'GX010123.MP4', 3), '012_GX010123_区間3.mp4');
  });

  test('元動画名は60文字に収め、絵文字などを途中で壊さない', () {
    expect(safeStem('${'動' * 70}.mp4'), '動' * 60);
    final emoji = safeStem('${'😀' * 70}.mp4');
    expect(emoji.runes.length, 60);
    expect(emoji, '😀' * 60);
  });

  test('実行ごとのフォルダ名は日時を2桁ずつ埋める', () {
    expect(batchFolderName(DateTime(2026, 9, 6, 1, 2, 3)),
        'kite_clips_20260906_010203');
    expect(batchFolderName(DateTime(2026, 12, 31, 23, 59, 59)),
        'kite_clips_20261231_235959');
  });

  test('既存と重なる名前は上書きせず _2, _3 を付ける', () {
    final existing = {'x.mp4', 'x_2.mp4'};
    expect(uniqueFileName('x.mp4', existing.contains), 'x_3.mp4');
    expect(uniqueFileName('y.mp4', existing.contains), 'y.mp4');
    expect(uniqueFileName('noext', {'noext'}.contains), 'noext_2');
  });
}
