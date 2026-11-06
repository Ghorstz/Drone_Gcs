"""
Chili (Sili) crop-health classifier service.

Loads the trained MobileNetV2 TFLite model and exposes a method to run
inference on a BGR numpy frame (as produced by CameraService), returning
aggregated 4-bucket crop-health percentages.
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Optional

import cv2
import numpy as np
from ai_edge_litert.interpreter import Interpreter

MODEL_DIR = Path(__file__).resolve().parent.parent / "models" / "chili"
MODEL_PATH = MODEL_DIR / "chili_classifier_v1.tflite"
CLASS_NAMES_PATH = MODEL_DIR / "class_names.json"

BUCKET_MAP = {
    "Chilli___healthy": "healthy",
    "Chilli __Yellowish": "wilting",
    "Chilli __Whitefly": "pest",
    "Chilli__Anthracnos": "disease",
    "Chilli__Damping_Off": "disease",
    "Chilli__Leaf_Curl_Virus": "disease",
    "Chilli__Leaf_Spot": "disease",
    "Chilli__Veinal_Mottle_Virus": "disease",
}


class ChiliClassifier:
    def __init__(self, bridge, log_sink=None, interval_s: float = 10.0):
        self.bridge = bridge
        self.log_sink = log_sink
        self.interval_s = interval_s
        self.stop_event = threading.Event()
        self.thread: Optional[threading.Thread] = None
        self.ready = False
        self.last_result: Optional[dict] = None

        self._log(f"Loading Chili classifier from {MODEL_PATH}")
        if not MODEL_PATH.exists() or not CLASS_NAMES_PATH.exists():
            self._log("Chili model files not found; classifier disabled", "WARN")
            return

        with open(CLASS_NAMES_PATH, "r") as f:
            self.class_names = json.load(f)

        self.interpreter = Interpreter(model_path=str(MODEL_PATH))
        self.interpreter.allocate_tensors()
        self.input_details = self.interpreter.get_input_details()
        self.output_details = self.interpreter.get_output_details()
        self.ready = True
        self._log("Chili classifier loaded successfully")

    def _log(self, message: str, level: str = "INFO"):
        print(f"[{level}] [ChiliClassifier] {message}")
        if self.log_sink:
            try:
                self.log_sink(level, f"[ChiliClassifier] {message}")
            except Exception:
                pass

    def _preprocess(self, frame_bgr: np.ndarray) -> np.ndarray:
        frame_rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
        resized = cv2.resize(frame_rgb, (224, 224))
        arr = resized.astype(np.float32)  # raw 0-255, model has preprocess_input baked in
        return np.expand_dims(arr, axis=0)

    def predict(self, frame_bgr: np.ndarray) -> Optional[dict]:
        if not self.ready:
            return None
        try:
            img_tensor = self._preprocess(frame_bgr)
            self.interpreter.set_tensor(self.input_details[0]['index'], img_tensor)
            self.interpreter.invoke()
            output = self.interpreter.get_tensor(self.output_details[0]['index'])[0]

            buckets = {"healthy": 0.0, "wilting": 0.0, "pest": 0.0, "disease": 0.0}
            for prob, cls_name in zip(output, self.class_names):
                buckets[BUCKET_MAP[cls_name]] += float(prob)

            # Convert to 0-100 scale
            result = {k: round(v * 100.0, 1) for k, v in buckets.items()}
            self.last_result = result
            return result
        except Exception as exc:
            self._log(f"Inference error: {exc}", "WARN")
            return None

    def start(self, camera_service):
        if not self.ready:
            self._log("Not starting loop; classifier not ready", "WARN")
            return
        if self.thread and self.thread.is_alive():
            return
        self.stop_event.clear()
        self.thread = threading.Thread(
            target=self._run, args=(camera_service,), daemon=True, name="chili_classifier"
        )
        self.thread.start()

    def stop(self):
        self.stop_event.set()

    def _run(self, camera_service):
        self._log(f"Starting inference loop (every {self.interval_s}s)")
        while not self.stop_event.is_set():
            jpeg_bytes = camera_service.frame()
            if jpeg_bytes:
                arr = np.frombuffer(jpeg_bytes, dtype=np.uint8)
                frame = cv2.imdecode(arr, cv2.IMREAD_COLOR)
                if frame is not None:
                    result = self.predict(frame)
                    if result:
                        try:
                            self.bridge.update_crop_health(
                                "sili",
                                healthy=result["healthy"],
                                wilting=result["wilting"],
                                disease=result["disease"],
                                pest=result["pest"],
                            )
                        except Exception as exc:
                            self._log(f"update_crop_health failed: {exc}", "WARN")
            time.sleep(self.interval_s)
