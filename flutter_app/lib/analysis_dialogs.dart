import 'package:flutter/material.dart';

import 'ai_segments.dart';
import 'analysis_service.dart';

String _time(int milliseconds) {
  final total = milliseconds ~/ 1000;
  return '${(total ~/ 60).toString().padLeft(2, '0')}:'
      '${(total % 60).toString().padLeft(2, '0')}';
}

String _seconds(int milliseconds) => (milliseconds / 1000).toStringAsFixed(1);

/// AI解析の速度測定結果。提案区間は表示のみで、一覧には追加しない。
class AnalysisResultDialog extends StatelessWidget {
  const AnalysisResultDialog(
      {super.key,
      required this.result,
      required this.segments,
      required this.sportId});
  final AnalysisResult result;
  final List<ProposedSegment> segments;
  final String sportId;

  @override
  Widget build(BuildContext context) => AlertDialog(
        title: const Text('AI解析の測定結果'),
        content: SingleChildScrollView(
            child: Column(
                mainAxisSize: MainAxisSize.min,
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
              Text('競技モデル：$sportId'),
              Text('解析したフレーム：${result.frames}枚（動画の長さ ${_time(result.durationMs)}）'),
              if (result.truncated)
                const Text('上限の300フレームに達したため、途中で打ち切りました。',
                    style: TextStyle(color: Color(0xffa33a3a))),
              const SizedBox(height: 10),
              const Text('所要時間', style: TextStyle(fontWeight: FontWeight.w700)),
              Text('合計：${_seconds(result.totalMs)}秒'),
              Text('　フレーム取り出し：${_seconds(result.extractMs)}秒'),
              Text('　CLIP推論：${_seconds(result.inferMs)}秒'),
              Text('　モデル読み込み：${_seconds(result.modelMs)}秒（初回のみ時間がかかります）'),
              Text(
                  '動画1分あたり：約${result.secondsPerVideoMinute.toStringAsFixed(1)}秒',
                  style: const TextStyle(fontWeight: FontWeight.w700)),
              const SizedBox(height: 10),
              Text('提案された区間：${segments.length}個',
                  style: const TextStyle(fontWeight: FontWeight.w700)),
              for (final segment in segments.take(20))
                Text('　${_time(segment.startMs)} 〜 ${_time(segment.endMs)}'
                    '（${_seconds(segment.lengthMs)}秒・平均${segment.meanScore.toStringAsFixed(2)}）'),
              if (segments.length > 20) Text('　ほか${segments.length - 20}個'),
              const SizedBox(height: 10),
              const Text('この測定では区間を一覧に追加しません。速度と提案内容の確認だけを行います。',
                  style: TextStyle(fontSize: 12, color: Colors.blueGrey)),
            ])),
        actions: [
          FilledButton(
              onPressed: () => Navigator.pop(context),
              child: const Text('閉じる')),
        ],
      );
}
