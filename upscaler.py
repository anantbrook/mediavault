import os
import urllib.request
from pathlib import Path

try:
    import cv2
    from cv2 import dnn_superres
    UPSCALER_AVAILABLE = True
except ImportError:
    UPSCALER_AVAILABLE = False
    print("[UPSCALER] OpenCV not found. Upscaling disabled.")

MODEL_DIR = Path(__file__).parent / "models"
MODEL_DIR.mkdir(exist_ok=True)
# Using EDSR x2 as default (lightweight, ~38MB)
MODEL_URL = "https://github.com/Saafke/EDSR_Tensorflow/raw/master/models/EDSR_x2.pb"
MODEL_PATH = MODEL_DIR / "EDSR_x2.pb"

def ensure_model():
    if not MODEL_PATH.exists():
        print("[UPSCALER] Downloading EDSR x2 model (this may take a minute)...")
        try:
            urllib.request.urlretrieve(MODEL_URL, str(MODEL_PATH))
            print("[UPSCALER] Model downloaded successfully.")
        except Exception as e:
            print(f"[UPSCALER] Failed to download model: {e}")
            return False
    return True

def upscale_image(filepath, scale=2):
    if not UPSCALER_AVAILABLE:
        return False, "Upscaler not available"
    if not ensure_model():
        return False, "Failed to load model"
    
    filepath = str(filepath)
    if not os.path.exists(filepath):
        return False, "File does not exist"

    try:
        # Load the image
        img = cv2.imread(filepath)
        if img is None:
            return False, "Failed to decode image"

        # Create an SR object
        sr = dnn_superres.DnnSuperResImpl_create()

        # Read and set the model
        sr.readModel(str(MODEL_PATH))
        sr.setModel("edsr", scale)

        # Upscale the image
        upscaled_img = sr.upsample(img)

        # Save it back (you could also save as _upscaled to keep original)
        base, ext = os.path.splitext(filepath)
        out_path = f"{base}_upscaled{ext}"
        cv2.imwrite(out_path, upscaled_img)
        
        return True, out_path
    except Exception as e:
        print(f"[UPSCALER] Error: {e}")
        return False, str(e)
