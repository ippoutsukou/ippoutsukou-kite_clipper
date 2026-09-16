#include "clip_embedder.h"

#include <windows.h>

#include <flutter/method_channel.h>
#include <flutter/standard_method_codec.h>
#include <onnxruntime_c_api.h>

#include <cmath>
#include <memory>
#include <string>
#include <vector>

namespace {

constexpr int kImageSize = 224;
constexpr int kEmbedDim = 512;
constexpr size_t kPlane = static_cast<size_t>(kImageSize) * kImageSize;
constexpr size_t kInputBytes = kPlane * 3;

// vendor/clip/SOURCE.txt と clip_embedder.py に合わせた前処理仕様。
const float kMean[3] = {0.48145466f, 0.4578275f, 0.40821073f};
const float kStd[3] = {0.26862954f, 0.26130258f, 0.27577711f};

// 同梱モデルは実行ファイルの隣の data/flutter_assets 以下に置かれる。
std::wstring ModelPath() {
  wchar_t buffer[MAX_PATH] = {0};
  DWORD length = GetModuleFileNameW(nullptr, buffer, MAX_PATH);
  std::wstring path(buffer, length);
  size_t slash = path.find_last_of(L"\\/");
  if (slash != std::wstring::npos) {
    path = path.substr(0, slash + 1);
  }
  return path + L"data\\flutter_assets\\assets\\clip\\vision_model_quantized.onnx";
}

// ONNX Runtime のセッションを1つだけ作って使い回す。失敗は文字列で返す（例外は使わない）。
class ClipSession {
 public:
  ~ClipSession() {
    if (ort_ == nullptr) {
      return;
    }
    if (memory_ != nullptr) {
      ort_->ReleaseMemoryInfo(memory_);
    }
    if (session_ != nullptr) {
      ort_->ReleaseSession(session_);
    }
    if (env_ != nullptr) {
      ort_->ReleaseEnv(env_);
    }
  }

  std::string Ensure() {
    if (session_ != nullptr) {
      return std::string();
    }
    ort_ = OrtGetApiBase()->GetApi(ORT_API_VERSION);
    if (ort_ == nullptr) {
      return "ONNX Runtime を初期化できません";
    }
    std::string failure = Check(ort_->CreateEnv(ORT_LOGGING_LEVEL_WARNING,
                                                "kite_clipper", &env_));
    if (!failure.empty()) {
      return failure;
    }
    OrtSessionOptions* options = nullptr;
    failure = Check(ort_->CreateSessionOptions(&options));
    if (!failure.empty()) {
      return failure;
    }
    failure = Check(ort_->CreateSession(env_, ModelPath().c_str(), options,
                                        &session_));
    ort_->ReleaseSessionOptions(options);
    if (!failure.empty()) {
      return failure;
    }
    return Check(ort_->CreateCpuMemoryInfo(OrtArenaAllocator, OrtMemTypeDefault,
                                           &memory_));
  }

  // RGBバイト列を正規化してCLIPに通し、L2正規化した埋め込みを返す。
  std::string Embed(const std::vector<uint8_t>& pixels,
                    std::vector<double>* out) {
    if (pixels.size() != kInputBytes) {
      return "画像の大きさが 224x224 RGB ではありません";
    }
    std::string failure = Ensure();
    if (!failure.empty()) {
      return failure;
    }
    std::vector<float> input(kInputBytes);
    for (size_t index = 0; index < kPlane; ++index) {
      for (size_t channel = 0; channel < 3; ++channel) {
        const float value =
            static_cast<float>(pixels[index * 3 + channel]) / 255.0f;
        input[channel * kPlane + index] =
            (value - kMean[channel]) / kStd[channel];
      }
    }

    const int64_t shape[4] = {1, 3, kImageSize, kImageSize};
    OrtValue* input_tensor = nullptr;
    failure = Check(ort_->CreateTensorWithDataAsOrtValue(
        memory_, input.data(), input.size() * sizeof(float), shape, 4,
        ONNX_TENSOR_ELEMENT_DATA_TYPE_FLOAT, &input_tensor));
    if (!failure.empty()) {
      return failure;
    }

    const char* input_names[] = {"pixel_values"};
    const char* output_names[] = {"image_embeds"};
    OrtValue* output_tensor = nullptr;
    failure = Check(ort_->Run(session_, nullptr, input_names,
                              const_cast<const OrtValue* const*>(&input_tensor),
                              1, output_names, 1, &output_tensor));
    ort_->ReleaseValue(input_tensor);
    if (!failure.empty()) {
      return failure;
    }

    float* data = nullptr;
    failure = Check(ort_->GetTensorMutableData(output_tensor,
                                               reinterpret_cast<void**>(&data)));
    if (failure.empty() && data != nullptr) {
      double norm = 0.0;
      for (int index = 0; index < kEmbedDim; ++index) {
        norm += static_cast<double>(data[index]) * data[index];
      }
      norm = std::sqrt(norm);
      out->resize(kEmbedDim);
      for (int index = 0; index < kEmbedDim; ++index) {
        (*out)[index] =
            norm > 1e-12 ? static_cast<double>(data[index]) / norm : 0.0;
      }
    }
    ort_->ReleaseValue(output_tensor);
    return failure;
  }

 private:
  std::string Check(OrtStatus* status) {
    if (status == nullptr) {
      return std::string();
    }
    std::string message(ort_->GetErrorMessage(status));
    ort_->ReleaseStatus(status);
    return message;
  }

  const OrtApi* ort_ = nullptr;
  OrtEnv* env_ = nullptr;
  OrtSession* session_ = nullptr;
  OrtMemoryInfo* memory_ = nullptr;
};

}  // namespace

void RegisterClipEmbedder(flutter::FlutterEngine* engine) {
  auto channel = std::make_shared<flutter::MethodChannel<flutter::EncodableValue>>(
      engine->messenger(), "kite_clipper/clip",
      &flutter::StandardMethodCodec::GetInstance());
  auto session = std::make_shared<ClipSession>();
  channel->SetMethodCallHandler(
      [session, channel](const flutter::MethodCall<flutter::EncodableValue>& call,
                         std::unique_ptr<flutter::MethodResult<flutter::EncodableValue>> result) {
        if (call.method_name() != "embed") {
          result->NotImplemented();
          return;
        }
        const auto* arguments = std::get_if<flutter::EncodableMap>(call.arguments());
        const std::vector<uint8_t>* pixels = nullptr;
        if (arguments != nullptr) {
          auto entry = arguments->find(flutter::EncodableValue("pixels"));
          if (entry != arguments->end()) {
            pixels = std::get_if<std::vector<uint8_t>>(&entry->second);
          }
        }
        if (pixels == nullptr) {
          result->Error("arguments", "画像データがありません");
          return;
        }
        std::vector<double> embedding;
        const std::string failure = session->Embed(*pixels, &embedding);
        if (!failure.empty()) {
          result->Error("embed", failure);
          return;
        }
        flutter::EncodableList values;
        values.reserve(embedding.size());
        for (double value : embedding) {
          values.push_back(flutter::EncodableValue(value));
        }
        result->Success(flutter::EncodableValue(values));
      });
}
