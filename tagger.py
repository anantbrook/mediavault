import os
import torch
from PIL import Image

try:
    from transformers import pipeline
    TAGGER_AVAILABLE = True
except ImportError:
    TAGGER_AVAILABLE = False
    print("[TAGGER] transformers not found. Auto-tagging disabled.")

# Load a lightweight vision model (e.g. mobilevit or a simple resnet trained on imagenet)
# We will use 'google/vit-base-patch16-224' for demonstration,
# although for anime/booru content deepdanbooru is better (but heavier).
_model_pipeline = None

def _get_pipeline():
    global _model_pipeline
    if _model_pipeline is None and TAGGER_AVAILABLE:
        try:
            print("[TAGGER] Loading model (first time might take a while)...")
            # For general image classification
            _model_pipeline = pipeline("image-classification", model="google/vit-base-patch16-224")
        except Exception as e:
            print(f"[TAGGER] Failed to load model: {e}")
            _model_pipeline = "FAILED"
    return _model_pipeline if _model_pipeline != "FAILED" else None

def auto_tag(filepath):
    if not TAGGER_AVAILABLE:
        return []
    pipe = _get_pipeline()
    if not pipe:
        return []
    
    filepath = str(filepath)
    ext = filepath.lower().split('.')[-1]
    if ext not in ("jpg", "jpeg", "png", "webp", "bmp"):
        return []
    
    try:
        with Image.open(filepath) as img:
            # Convert to RGB to avoid issues with alpha channels
            img_rgb = img.convert("RGB")
            results = pipe(img_rgb, top_k=5)
            # return the labels
            tags = [res['label'] for res in results]
            return tags
    except Exception as e:
        print(f"[TAGGER] Error tagging {filepath}: {e}")
        return []

