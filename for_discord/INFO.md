# AI Image Detector - For Discord

**Version**: 1.0  
**Trained**: February 2026  
**Accuracy**: 99%+  
**Type**: Pixel-based detection (no EXIF/metadata)

## What This Is

A standalone AI image detector that returns a **scalar probability score** (0.0 to 1.0):
- **0.0** = Real image  
- **1.0** = AI-generated  
- Intermediate values = confidence level

Perfect for Discord bots, automated moderation, and bulk image scanning.

## Files in This Package

```
for_discord/
├── detect.py              # Main inference script
├── best.pkl               # Trained model (99%+ accuracy)
├── requirements.txt       # Python dependencies
├── QUICKSTART.md         # Quick start guide ⭐
├── README.md             # Full documentation
├── metrics.json          # Model performance stats
└── features*.py          # Feature extraction (internal)
```

## Quick Start

### 1. Install Dependencies

```bash
pip install -r requirements.txt
```

### 2. Run Detection

```bash
python detect.py image.png
```

**Output:**
```
0.987654
```

That's it! 0.987654 means 98.77% confidence it's AI-generated.

## Key Features

✅ **Scalar Output** - Returns probability, not binary yes/no  
✅ **No Metadata** - Works on screenshots, crops, resized images  
✅ **High Accuracy** - 99%+ on test set  
✅ **Fast** - ~1-2 seconds per image  
✅ **Self-Contained** - No external APIs needed  
✅ **URL Support** - Can detect from URLs directly  

## Model Info

- **Features**: 751 pixel-based features
- **Families**: Color, Frequency, Noise, Texture, Gradient, Forensic, Model-Specific, NSS, CFA, Self-Similarity, Residual
- **Training**: Balanced dataset, XGBoost + Random Forest + Logistic Regression ensemble
- **Protection**: Metadata features excluded to prevent spurious correlations

## Support

See **QUICKSTART.md** for quick examples  
See **README.md** for full documentation

---

**Model trained by subset_new pipeline**  
**Date**: February 8, 2026
