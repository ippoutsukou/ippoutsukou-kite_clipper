import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:kite_clipper_flutter/editor_controls.dart';
import 'package:kite_clipper_flutter/editor_viewport.dart';

void main() {
  for (final size in [const Size(1280, 720), const Size(390, 844), const Size(844, 390)]) {
    testWidgets('再生バーと長文通知の収まり $size', (tester) async {
      tester.view.physicalSize = size;
      tester.view.devicePixelRatio = 1;
      addTearDown(tester.view.resetPhysicalSize);
      addTearDown(tester.view.resetDevicePixelRatio);
      var played = false;
      var muted = false;
      int? seek;
      await tester.pumpWidget(MaterialApp(theme: ThemeData(splashFactory: InkRipple.splashFactory), home: Scaffold(
        appBar: AppBar(title: const Text('クリップを確認')),
        body: Column(children: [
          EditorNotice(failed: true, details: List.filled(30, '診断情報').join('\n'), onDismiss: () {}),
          Expanded(child: EditorViewport(
            preview: const ColoredBox(key: Key('preview'), color: Colors.black),
            controls: [
              PlaybackBar(positionMs: 1000, durationMs: 10000, playing: false,
                muted: false, enabled: true, onToggle: () => played = true,
                onMute: () => muted = true, onSeek: (v) => seek = v),
              const SizedBox(height: 110, child: Center(child: Text('範囲調整'))),
            ],
          )),
          const SizedBox(height: 76, child: Center(child: Text('1本保存'))),
        ]),
      )));
      expect(tester.takeException(), isNull);
      expect(tester.getSize(find.byKey(const Key('preview'))).height, greaterThan(0));
      expect(tester.getSize(find.byType(EditorNotice)).height, 48);
      await tester.tap(find.byTooltip('再生'));
      await tester.tap(find.byTooltip('ミュート'));
      await tester.tap(find.byType(Slider));
      expect(played, isTrue);
      expect(muted, isTrue);
      expect(seek, isNotNull);
      await tester.tap(find.text('詳細'));
      await tester.pumpAndSettle();
      expect(find.text('エラーの詳細'), findsOneWidget);
      expect(tester.takeException(), isNull);
      await tester.tap(find.text('閉じる'));
      await tester.pumpAndSettle();
      expect(find.byType(AlertDialog), findsNothing);
    });
  }
}
