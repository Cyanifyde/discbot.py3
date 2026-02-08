# AI Image Detector - Discord Deployment Package

This folder contains everything needed to run AI image detection inference using the trained subset_new model.

## What's Included

- **best.pkl** - Trained model (751 features, 99%+ accuracy)
- **detect.py** - Simple inference script
- **features*.py** - Feature extraction modules
- **metrics.json** - Model performance metrics

## Model Info

- **Accuracy**: 99%+ on held-out test set
- **Features**: 751 pixel-based features (no EXIF/metadata)
- **Families**: 12 AI-discriminative feature families
- **Max Dimension**: 512px (images resized preserving aspect ratio)
- **Protection**: Metadata-proof (works on crops, screenshots, resized images)

### Feature Families

1. **Color** - Histogram moments, inter-channel correlations
2. **Frequency** - FFT spectral profiles, GAN checkerboard artifacts
3. **Spectral Diffusion** - 1D power spectrum characteristics
4. **Noise** - Diffusion noise vs camera sensor noise patterns
5. **Texture** - LBP/GLCM/Haralick microtexture analysis
6. **Gradient** - Gradient coherence, color bleeding
7. **Forensic** - PRNU, JPEG quantization inconsistencies
8. **Model-Specific** - GAN/diffusion fingerprints
9. **NSS** - Natural scene statistics (MSCN/GGD/AGGD)
10. **CFA** - Color filter array demosaicing traces
11. **Self-Similarity** - Patch repetition, cross-attention leakage
12. **Residual** - Denoising residual patterns

## Installation

### 1. Install Python Dependencies

```bash
pip install -r requirements.txt
```

**Minimum Requirements:**
- Python 3.8+
- numpy, scipy, scikit-learn, Pillow, opencv-python

### 2. Verify Installation

```bash
python detect.py --help
```

## Usage

### Basic Usage

```bash
python detect.py <image_path>
```

**Output:** Single float value between 0.0 and 1.0
- `0.0` = definitely real image
- `1.0` = definitely AI-generated
- `0.5` = uncertain

### Examples

```bash
# Local file
python detect.py my_image.png
# Output: 0.987654

# URL
python detect.py https://example.com/image.jpg
# Output: 0.123456

# Verbose mode (detailed output)
python detect.py my_image.png --verbose
# Output includes verdict and probabilities
```

### Verbose Mode

```bash
python detect.py image.png -v
```

Shows:
- AI Probability
- Real Probability
- Verdict (High Confidence, Likely AI, etc.)

### Integration Examples

#### Python Script

```python
import subprocess

def detect_ai(image_path):
    """Returns AI probability (0.0 to 1.0)"""
    result = subprocess.run(
        ['python', 'detect.py', image_path],
        capture_output=True,
        text=True
    )
    return float(result.stdout.strip())

# Usage
prob = detect_ai('image.png')
if prob > 0.9:
    print("AI-generated with high confidence")
elif prob > 0.7:
    print("Likely AI")
elif prob < 0.3:
    print("Real image with high confidence")
else:
    print("Uncertain")
```

#### Discord Bot (discord.py)

```python
import discord
import subprocess
from discord.ext import commands

bot = commands.Bot(command_prefix='!')

@bot.command()
async def checkimage(ctx):
    """Check if attached image is AI-generated"""
    if not ctx.message.attachments:
        await ctx.send("Please attach an image!")
        return
    
    attachment = ctx.message.attachments[0]
    
    # Download to temp file
    await attachment.save('temp_image.png')
    
    # Run detection
    result = subprocess.run(
        ['python', 'detect.py', 'temp_image.png'],
        capture_output=True,
        text=True
    )
    
    ai_prob = float(result.stdout.strip())
    
    if ai_prob > 0.9:
        verdict = f"🤖 **AI Generated** ({ai_prob:.1%} confidence)"
    elif ai_prob > 0.7:
        verdict = f"⚠️ **Likely AI** ({ai_prob:.1%} confidence)"
    elif ai_prob < 0.3:
        verdict = f"📷 **Real Image** ({(1-ai_prob):.1%} confidence)"
    elif ai_prob < 0.5:
        verdict = f"✅ **Likely Real** ({(1-ai_prob):.1%} confidence)"
    else:
        verdict = f"❓ **Uncertain** ({max(ai_prob, 1-ai_prob):.1%})"
    
    await ctx.send(verdict)

bot.run('YOUR_TOKEN')
```

#### Bash Script

```bash
#!/bin/bash
# Check multiple images

for img in *.png *.jpg; do
    prob=$(python detect.py "$img")
    echo "$img: $prob"
    
    if (( $(echo "$prob > 0.9" | bc -l) )); then
        echo "  -> AI Generated"
    fi
done
```

## How It Works

1. **Load Model** - Loads pre-trained ensemble (GBM + RF + LR)
2. **Load Image** - Opens image, converts to RGB
3. **Resize** - Scales to max 512px (preserves aspect ratio)
4. **Extract Features** - Computes 751 pixel-based features
5. **Predict** - Runs through calibrated classifier
6. **Output** - Returns AI probability as scalar

## Performance

From `metrics.json`:

- **Test Accuracy**: 99%+
- **Test Precision**: 99%+
- **Test Recall**: 99%+
- **ROC-AUC**: 0.999+
- **Cross-Validation**: 98.5%+ (5-fold)

### Confusion Matrix (typical)
```
              Predicted
              Real    AI
Actual Real   [TN]    [FP]
       AI     [FN]    [TP]
```

Very low false positive/negative rates.

## Important Notes

### What This Model Detects

✅ **Does Detect:**
- Diffusion models (Stable Diffusion, DALL-E, Midjourney)
- GAN outputs (StyleGAN, etc.)
- Pixel-level AI generation artifacts
- Works on crops, screenshots, resized images

❌ **Does NOT Use:**
- EXIF metadata
- File size or dimensions
- Image format
- Timestamps or GPS data

### Limitations

- **Face swaps**: May not detect if only face is AI
- **Heavy editing**: Extensive photo editing can confuse results
- **Very small images**: Best with >256px
- **Heavily compressed**: JPEG quality <50 may reduce accuracy

### When to Trust Results

- ✅ **High confidence** (prob > 0.9 or < 0.1): Very reliable
- ⚠️ **Medium** (0.7-0.9 or 0.1-0.3): Good indicator but verify
- ❓ **Uncertain** (0.4-0.6): Ambiguous, manual review recommended

## Troubleshooting

### "ModuleNotFoundError"

```bash
pip install -r requirements.txt
```

### "Model file not found"

Ensure `best.pkl` is in the same directory as `detect.py`

### Slow Performance

- First run compiles JIT functions (slow)
- Subsequent runs are faster
- Consider batch processing for multiple images

### Memory Issues

Model loads ~50-100MB. If memory constrained:
- Process one image at a time
- Use smaller images (will resize anyway)

## File Descriptions

| File | Purpose |
|------|---------|
| `best.pkl` | Trained model (weights, scaler, feature names) |
| `detect.py` | Main inference script |
| `features.py` | Feature extraction orchestrator |
| `features_*.py` | Individual feature family modules |
| `image_precomputed.py` | Cached image computations |
| `utils.py` | Utility functions |
| `gpu_utils.py` | GPU acceleration (optional) |
| `jit_utils.py` | JIT-compiled functions for speed |
| `metrics.json` | Model performance metrics |

## Model Training Info

See parent `subset_new/` folder for training pipeline:
- Trained on balanced AI + real dataset
- 12 AI-discriminative feature families
- Metadata features explicitly excluded
- Progressive scaling with overfitting protection
- Ensemble model with isotonic calibration

## License & Credits

This model is part of the AI image detection research project.

**Trained**: February 2026
**Version**: subset_new v1.0
**Features**: 751 (12 families)
**Accuracy**: 99%+

---

For issues or questions, see the main project documentation.
