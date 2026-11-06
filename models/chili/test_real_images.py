import json
import numpy as np
from PIL import Image
from ai_edge_litert.interpreter import Interpreter

MODEL_PATH = "/home/rogue/Projects/Drone_Gcs/models/chili/chili_classifier_v1.tflite"
CLASS_NAMES_PATH = "/home/rogue/Projects/Drone_Gcs/models/chili/class_names.json"

with open(CLASS_NAMES_PATH, "r") as f:
    class_names = json.load(f)

interpreter = Interpreter(model_path=MODEL_PATH)
interpreter.allocate_tensors()
input_details = interpreter.get_input_details()
output_details = interpreter.get_output_details()

BUCKET_MAP = {
    "Chilli___healthy": "Healthy",
    "Chilli __Yellowish": "Wilting",
    "Chilli __Whitefly": "Pest",
    "Chilli__Anthracnos": "Disease",
    "Chilli__Damping_Off": "Disease",
    "Chilli__Leaf_Curl_Virus": "Disease",
    "Chilli__Leaf_Spot": "Disease",
    "Chilli__Veinal_Mottle_Virus": "Disease",
}

def preprocess_image(path):
    img = Image.open(path).convert("RGB").resize((224, 224))
    arr = np.array(img).astype(np.float32)
    # NOTE: do NOT normalize here — preprocess_input is already baked into the saved model.
    # Feed raw 0-255 float32 values, matching what image_dataset_from_directory produced in Colab.
    return np.expand_dims(arr, axis=0)

def predict(path, true_label_hint):
    img_tensor = preprocess_image(path)
    interpreter.set_tensor(input_details[0]['index'], img_tensor)
    interpreter.invoke()
    output = interpreter.get_tensor(output_details[0]['index'])[0]

    print(f"\n=== {path} (expected: {true_label_hint}) ===")
    top_idx = int(np.argmax(output))
    print(f"Top prediction: {class_names[top_idx]} ({output[top_idx]*100:.2f}%)")

    buckets = {"Healthy": 0.0, "Wilting": 0.0, "Pest": 0.0, "Disease": 0.0}
    for prob, cls_name in zip(output, class_names):
        buckets[BUCKET_MAP[cls_name]] += float(prob)

    print("Bucketed result:")
    for bucket, val in buckets.items():
        print(f"  {bucket}: {val*100:.2f}%")

predict("/home/rogue/Projects/Drone_Gcs/models/chili/healthy_sample.jpg", "Healthy")
predict("/home/rogue/Projects/Drone_Gcs/models/chili/whitefly_sample.jpg", "Pest")
predict("/home/rogue/Projects/Drone_Gcs/models/chili/anthracnos_sample.jpg", "Disease")
