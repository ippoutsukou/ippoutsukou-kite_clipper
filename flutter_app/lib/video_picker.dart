import 'dart:io';

import 'package:file_selector/file_selector.dart';
import 'package:flutter/services.dart';

/// 動画を選ぶ。Androidは自前の選択画面を使う。
///
/// file_selector のAndroid実装は、選んだファイルの中身を丸ごとメモリに載せて
/// Dart側へ渡すため、大きい動画（数十MB以上）でアプリが落ちる。
/// Androidでは場所（URI）だけを受け取り、ネイティブ側で少しずつコピーする。
class VideoPicker {
  static const channel = MethodChannel('kite_clipper/picker');

  Future<List<String>> pick() async {
    if (Platform.isAndroid) {
      final paths = await channel.invokeListMethod<String>('pickVideos');
      return paths ?? const [];
    }
    final files = await openFiles(acceptedTypeGroups: [
      const XTypeGroup(
          label: '動画',
          extensions: ['mp4', 'mov', 'mkv', 'webm'],
          mimeTypes: ['video/*']),
    ]);
    return [for (final file in files) file.path];
  }
}
