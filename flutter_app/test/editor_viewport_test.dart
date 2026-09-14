import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:kite_clipper_flutter/editor_viewport.dart';

void main() {
  test('開始・終了のうち動かした側の時刻をプレビューする', () {
    expect(rangePreviewPosition(const RangeValues(0, 9000), const RangeValues(1200, 9000), 10000), 1200);
    expect(rangePreviewPosition(const RangeValues(1200, 9000), const RangeValues(1200, 7200), 10000), 7200);
    expect(rangePreviewPosition(const RangeValues(1200, 7200), const RangeValues(1200, 10000), 10000), 9999);
  });

  for (final size in [const Size(1920, 1080), const Size(1280, 720), const Size(390, 844), const Size(844, 390)]) {
    testWidgets('動画より操作欄を優先して画面内に収める $size', (tester) async {
      tester.view.physicalSize = size;
      tester.view.devicePixelRatio = 1;
      addTearDown(tester.view.resetPhysicalSize);
      addTearDown(tester.view.resetDevicePixelRatio);
      await tester.pumpWidget(MaterialApp(home: Scaffold(
        appBar: AppBar(title: const Text('クリップを確認')),
        body: Column(children: [
          Expanded(child: Padding(padding: const EdgeInsets.all(16), child: EditorViewport(
            preview: const ColoredBox(key: Key('preview'), color: Colors.black),
            controls: [
              const SizedBox(height: 8),
              const Text('保存する範囲'),
              RangeSlider(key: const Key('range'), values: const RangeValues(0.2, 0.8), onChanged: (_) {}),
              const Text('開始 00:02.000   終了 00:08.000'),
            ],
          ))),
          const SizedBox(height: 76, child: Center(child: Text('1本保存'))),
        ]),
      )));
      expect(tester.takeException(), isNull);
      final bar = tester.getRect(find.byKey(const Key('range')));
      final preview = tester.getRect(find.byKey(const Key('preview')));
      expect(bar.bottom, lessThan(size.height - 76));
      expect(preview.height, greaterThan(0));
      expect(preview.bottom, lessThan(bar.top));
    });
  }
}
