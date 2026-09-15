import 'dart:io';

// ビルド環境の診断専用。既存ファイルは開かず、新規ディレクトリにだけ書く。
Future<void> main() async {
  final root = Directory.fromUri(Platform.script.resolve('../../'));
  for (final base in [
    '${root.path}/out/flutter-proof',
    '${root.path}/flutter_app/.dart_tool/flutter_build'
  ]) {
    try {
      final dir = await Directory(base).createTemp('write_probe_');
      for (final suffix in ['bin', 'dill']) {
        final file = File('${dir.path}/probe.$suffix');
        await file.writeAsBytes(List.filled(1024, 42), flush: true);
        print('$base .$suffix: ${await file.length()} bytes OK');
      }
    } catch (e) {
      print('$base: $e');
      exitCode = 1;
    }
  }
}
