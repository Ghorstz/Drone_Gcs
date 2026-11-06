import json
import numpy as np
from ai_edge_litert.interpreter import Interpreter

MODEL_PATH = "/home/rogue/Projects/Drone_Gcs/models/chili/chili_classifier_v1.tflite"
CLASS_NAMES_PATH = "/home/rogue/Projects/Drone_Gcs/models/chili/class_names.json"

# Load class names
with open(CLASS_NAMES_PATH, "r") as f:
    class_names = json.load(f)
print("Loaded class names:", class_names)

# Load the TFLite model via LiteRT
interpreter = Interpreter(model_path=MODEL_PATH)
interpreter.allocate_tensors()

input_details = interpreter.get_input_details()
output_details = interpreter.get_output_details()

print("\nInput details:", input_details[0]['shape'], input_details[0]['dtype'])
print("Output details:", output_details[0]['shape'], output_details[0]['dtype'])

# Run inference on a fake random image (just to prove the pipeline executes end-to-end)
fake_image = np.random.rand(1, 224, 224, 3).astype(np.float32)

interpreter.set_tensor(input_details[0]['index'], fake_image)
interpreter.invoke()
output = interpreter.get_tensor(output_details[0]['index'])[0]

print("\nRaw output (should be 8 probabilities summing to ~1.0):")
for name, prob in zip(class_names, output):
    print(f"  {name}: {prob*100:.2f}%")

print(f"\nSum: {sum(output)*100:.2f}%")
