import 'package:flutter/material.dart';

import 'ai_segments.dart';
import 'analysis_service.dart';

String _time(int milliseconds) {
  final total = milliseconds ~/ 1000;
  return '${(total ~/ 60).toString().padLeft(2, '0')}:'
      '${(total % 60).toString().padLeft(2, '0')}';
}

String _seconds(int milliseconds) => (milliseconds / 1000).toStringAsFixed(1);

/// 解析の開始条件。競技とフレーム間隔を選ぶ。
class AnalysisRequest {
  const AnalysisRequest(this.sportId, this.intervalMs, {this.reanalyze = false});
  final String sportId;
  final int intervalMs;

  /// 保存済みの結果があっても解析し直すか。
  final bool reanalyze;
}

class AnalysisStartDialog extends StatefulWidget {
  const AnalysisStartDialog(
      {super.key, required this.sportId, this.savedSportId, this.savedIntervalMs});
  final String sportId;

  /// 保存済み結果がある場合の競技と間隔（ない場合は null）。
  final String? savedSportId;
  final int? savedIntervalMs;
  @override
  State<AnalysisStartDialog> createState() => _AnalysisStartDialogState();
}

class _AnalysisStartDialogState extends State<AnalysisStartDialog> {
  late String sportId = widget.sportId;
  late int intervalMs = widget.savedIntervalMs ?? 1000;

  bool get hasSaved =>
      widget.savedSportId == sportId && widget.savedIntervalMs == intervalMs;

  @override
  Widget build(BuildContext context) => AlertDialog(
        title: const Text('AI解析'),
        content: SingleChildScrollView(
            child: Column(
                mainAxisSize: MainAxisSize.min,
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
              const Text('競技', style: TextStyle(fontWeight: FontWeight.w700)),
              Wrap(spacing: 8, children: [
                for (final id in sportModelIds)
                  ChoiceChip(
                      label: Text(id),
                      selected: id == sportId,
                      onSelected: (_) => setState(() => sportId = id)),
              ]),
              const SizedBox(height: 12),
              const Text('フレーム間隔',
                  style: TextStyle(fontWeight: FontWeight.w700)),
              Wrap(spacing: 8, children: [
                for (final value in [1000, 2000])
                  ChoiceChip(
                      label: Text('${value ~/ 1000}秒'),
                      selected: value == intervalMs,
                      onSelected: (_) => setState(() => intervalMs = value)),
              ]),
              const Text('2秒にすると時間はおよそ半分、区間の切れ目は粗くなります。',
                  style: TextStyle(fontSize: 12, color: Colors.blueGrey)),
              if (hasSaved) ...[
                const SizedBox(height: 12),
                const Text('この条件の解析結果が保存されています。すぐに区間を作れます。',
                    style: TextStyle(fontSize: 12, color: Color(0xff147d55))),
              ],
            ])),
        actions: [
          TextButton(
              onPressed: () => Navigator.pop(context),
              child: const Text('キャンセル')),
          if (hasSaved)
            TextButton(
                onPressed: () => Navigator.pop(
                    context, AnalysisRequest(sportId, intervalMs, reanalyze: true)),
                child: const Text('解析し直す')),
          FilledButton(
              onPressed: () =>
                  Navigator.pop(context, AnalysisRequest(sportId, intervalMs)),
              child: Text(hasSaved ? '保存済みを使う' : '解析する')),
        ],
      );
}

/// 解析中の進捗と中止。
class AnalysisProgressDialog extends StatelessWidget {
  const AnalysisProgressDialog(
      {super.key,
      required this.done,
      required this.total,
      required this.cancelling,
      required this.onCancel});
  final int done;
  final int total;
  final bool cancelling;
  final VoidCallback onCancel;

  @override
  Widget build(BuildContext context) => AlertDialog(
        content: Column(mainAxisSize: MainAxisSize.min, children: [
          Text(cancelling
              ? '中止しています…（処理中の1枚が終わるまで待っています）'
              : total > 0
                  ? '$done / $total 枚を解析中'
                  : '解析を準備しています…'),
          const SizedBox(height: 12),
          LinearProgressIndicator(
              value: total > 0 ? (done / total).clamp(0.0, 1.0) : null),
        ]),
        actions: [
          TextButton(
              onPressed: cancelling ? null : onCancel,
              child: const Text('中止')),
        ],
      );
}

/// 解析結果。ここから提案区間を一覧へ追加できる。
class AnalysisResultDialog extends StatelessWidget {
  const AnalysisResultDialog(
      {super.key,
      required this.result,
      required this.segments,
      required this.sportId,
      required this.fromSaved,
      this.otherCounts = const {},
      this.onApply});
  final AnalysisResult result;
  final List<ProposedSegment> segments;
  final String sportId;

  /// 保存済みの結果から作った提案か。
  final bool fromSaved;

  /// 他の競技モデルで採点した場合の提案数（キーは競技ID）。
  final Map<String, int> otherCounts;
  final VoidCallback? onApply;

  @override
  Widget build(BuildContext context) => AlertDialog(
        title: Text(result.cancelled ? 'AI解析を中止しました' : 'AI解析の結果'),
        content: SingleChildScrollView(
            child: Column(
                mainAxisSize: MainAxisSize.min,
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
              Text('競技モデル：$sportId'),
              Text(
                  '解析したフレーム：${result.frames}枚（動画の長さ ${_time(result.durationMs)}）'),
              if (result.frames >= 2)
                Text('実際の間隔：約${(result.averageIntervalMs / 1000).toStringAsFixed(1)}秒'),
              if (result.cancelled)
                const Text('中止したため、途中までの結果です。',
                    style: TextStyle(color: Color(0xffa33a3a))),
              if (result.truncated)
                const Text('上限に達したため、途中で打ち切りました。',
                    style: TextStyle(color: Color(0xffa33a3a))),
              if (fromSaved)
                const Text('保存済みの解析結果を使いました。',
                    style: TextStyle(fontSize: 12, color: Color(0xff147d55)))
              else ...[
                const SizedBox(height: 10),
                const Text('所要時間',
                    style: TextStyle(fontWeight: FontWeight.w700)),
                Text('合計：${_seconds(result.totalMs)}秒'
                    '（${result.probeMs > 0 ? '下調べ ${_seconds(result.probeMs)}秒 ／ ' : ''}'
                    '取り出し ${_seconds(result.extractMs)}秒 ／ 推論 ${_seconds(result.inferMs)}秒）'),
                Text('動画1分あたり：約${result.secondsPerVideoMinute.toStringAsFixed(1)}秒'),
              ],
              const SizedBox(height: 10),
              Text('提案された区間：${segments.length}個',
                  style: const TextStyle(fontWeight: FontWeight.w700)),
              for (final segment in segments.take(20))
                Text('　${_time(segment.startMs)} 〜 ${_time(segment.endMs)}'
                    '（${_seconds(segment.lengthMs)}秒・平均${segment.meanScore.toStringAsFixed(2)}）'),
              if (segments.length > 20) Text('　ほか${segments.length - 20}個'),
              if (otherCounts.isNotEmpty) ...[
                const SizedBox(height: 10),
                const Text('他の競技モデルでの提案数',
                    style: TextStyle(fontWeight: FontWeight.w700)),
                for (final entry in otherCounts.entries)
                  Text('　${entry.key}：${entry.value}個'),
                const Text('合わないモデルを選ぶと0個や全体が1区間になります。撮り方に近いものを選んでください。',
                    style: TextStyle(fontSize: 12, color: Colors.blueGrey)),
              ],
              const SizedBox(height: 10),
              const Text('追加すると、区間として編集・保存できます。元動画は変更しません。',
                  style: TextStyle(fontSize: 12, color: Colors.blueGrey)),
            ])),
        actions: [
          TextButton(
              onPressed: () => Navigator.pop(context),
              child: const Text('閉じる')),
          FilledButton(
              onPressed: segments.isEmpty || onApply == null
                  ? null
                  : () {
                      Navigator.pop(context);
                      onApply!();
                    },
              child: const Text('区間として追加')),
        ],
      );
}
