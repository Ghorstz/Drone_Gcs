#!/usr/bin/env python3
import json
from pathlib import Path
import cv2, numpy as np
from ai_edge_litert.interpreter import Interpreter

D = Path("models/chili")
names = json.load(open(D / "class_names.json"))
it = Interpreter(model_path=str(D / "chili_classifier_v1.tflite"))
it.allocate_tensors()
inp, out = it.get_input_details()[0], it.get_output_details()[0]

for folder in ("nonplant_test", "plant_test"):
    files = sorted(Path(folder).glob("*.jpg"))
    print(f"--- {folder}: {len(files)} images")
    for f in files:
        img = cv2.imread(str(f))
        if img is None:
            continue
        x = np.expand_dims(cv2.resize(cv2.cvtColor(img, cv2.COLOR_BGR2RGB), (224, 224)).astype(np.float32), 0)
        it.set_tensor(inp["index"], x)
        it.invoke()
        p = it.get_tensor(out["index"])[0]
        print(f"{f.name:28s} top={names[int(p.argmax())]:30s} conf={p.max():.2f}")
