import 'package:flutter/material.dart';

int? parseClipTime(String text) {
  final match =
      RegExp(r'^(\d{1,6}):([0-5]\d)(?:\.(\d{1,3}))?$').firstMatch(text.trim());
  if (match == null) return null;
  return int.parse(match[1]!) * 60000 +
      int.parse(match[2]!) * 1000 +
      int.parse((match[3] ?? '').padRight(3, '0'));
}

String? validateClipTime(int? value,
    {required bool start, required int other, required int duration}) {
  if (value == null) return '分:秒.ミリ秒で入力してください（例 00:02.500）';
  if (value < 0 || value > duration) return '動画の長さの範囲内で入力してください';
  if (start ? other - value < 100 : value - other < 100) {
    return '開始から終了まで0.1秒以上の区間を指定してください';
  }
  return null;
}

class TimeEntryDialog extends StatefulWidget {
  const TimeEntryDialog(
      {super.key,
      required this.start,
      required this.initial,
      required this.other,
      required this.duration});
  final bool start;
  final String initial;
  final int other, duration;
  @override
  State<TimeEntryDialog> createState() => _TimeEntryDialogState();
}

class _TimeEntryDialogState extends State<TimeEntryDialog> {
  late final controller = TextEditingController(text: widget.initial);
  String? error;
  @override
  void dispose() {
    controller.dispose();
    super.dispose();
  }

  void apply() {
    final value = parseClipTime(controller.text);
    final message = validateClipTime(value,
        start: widget.start, other: widget.other, duration: widget.duration);
    if (message != null) {
      setState(() => error = message);
      return;
    }
    Navigator.pop(context, value);
  }

  @override
  Widget build(BuildContext context) => AlertDialog(
        title: Text(widget.start ? '開始時刻を変更' : '終了時刻を変更'),
        content: SingleChildScrollView(
            child: Column(mainAxisSize: MainAxisSize.min, children: [
          TextField(
              controller: controller,
              autofocus: true,
              keyboardType: TextInputType.datetime,
              textInputAction: TextInputAction.done,
              onSubmitted: (_) => apply(),
              decoration: InputDecoration(
                  labelText: '分:秒.ミリ秒',
                  hintText: '00:02.500',
                  errorText: error,
                  errorMaxLines: 3)),
          const SizedBox(height: 12),
          const Text('高速コピーでは、指定時刻と保存境界がずれる場合があります。',
              style: TextStyle(fontSize: 12, color: Colors.blueGrey)),
        ])),
        actions: [
          TextButton(
              onPressed: () => Navigator.pop(context),
              child: const Text('キャンセル')),
          FilledButton(onPressed: apply, child: const Text('適用')),
        ],
      );
}
