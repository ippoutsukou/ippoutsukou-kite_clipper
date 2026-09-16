#ifndef RUNNER_CLIP_EMBEDDER_H_
#define RUNNER_CLIP_EMBEDDER_H_

#include <flutter/flutter_engine.h>

// CLIP画像エンコーダー(ONNX)をDartから呼べるようにする。
// チャンネル kite_clipper/clip のメソッド embed:
//   入力 pixels: 224*224*3 バイトのRGB
//   出力 L2正規化済みの512個の数値
void RegisterClipEmbedder(flutter::FlutterEngine* engine);

#endif  // RUNNER_CLIP_EMBEDDER_H_
