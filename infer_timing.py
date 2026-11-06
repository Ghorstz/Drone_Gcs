#!/usr/bin/env python3
import json, time, statistics
from pathlib import Path
import cv2, numpy as np
from ai_edge_litert.interpreter import Interpreter

D = Path("models/chili")
names = json.load(open(D / "class_names.json"))
it = Interpreter(model_path=str(D / "chili_classifier_v1.tflite"))
it.allocate_tensors()
inp, out = it.get_input_details()[0], it.get_output_details()[0]

def run(path):
    img = cv2.cvtColor(cv2.imread(str(path)), cv2.COLOR_BGR2RGB)
    x = np.expand_dims(cv2.resize(img, (224, 224)).astype(np.float32), 0)
    t = time.perf_counter()
    it.set_tensor(inp["index"], x)
    it.invoke()
    p = it.get_tensor(out["index"])[0]
    return (time.perf_counter() - t) * 1000, names[int(p.argmax())], float(p.max())

for f in sorted(D.glob("*_sample.jpg")):
    run(f)  # warm-up
    ms = [run(f)[0] for _ in range(50)]
    _, label, conf = run(f)
    print(f"{f.name}: mean={statistics.mean(ms):.1f} ms max={max(ms):.1f} ms top={label} conf={conf:.2f}")
